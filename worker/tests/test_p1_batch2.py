"""第二轮 P1/P2 修复的行为锁测试。

对应 yt-dev-review 第二轮的修复项：

1. **Provider 共享 HTTP client**：ai/tts/image/asr/a2a 复用同一
   :class:`httpx.AsyncClient`，一次进程内不再每条请求重握手 + 重探代理；
   关停钩子 :func:`aclose_shared_async_client` 释放。
2. **代理探测 TTL 缓存**：5 分钟窗口内只真探一次；测试可通过
   :func:`reset_proxy_probe_cache` 归零。
3. **MCP stderr 泵**：后台持续读入环形缓冲，避免 ``PIPE`` 缓冲区打满时
   子进程与主进程互锁到超时。
4. **lease ``sweep_expired`` N+1 → 一条 ``UPDATE ... WHERE id IN (?,?,...)``**。
5. **backup 大文件 IO 走线程池**：copy2 / stat 不再冻结事件循环。
6. **project_io**：4 段 INSERT 包进 ``with conn:``（中途失败 rollback，
   不留半截项目 + 悬着的事务）；bundle JSON 成员读入有形状校验；zip bomb
   有单文件字节上限；媒体解包按 64 KB 分块 copy。
7. **import_source content_hash 必须是 str**：类型不对直接拒，
   杜绝"传 dict 也能落库 → 误命中别人 hash → dedup 静默删刚下载好的素材"。
8. **P2 ``_resolve_stepwork_home`` 去重**：4 个 handler 都从
   :func:`worker.runtime.cleanup.resolve_stepwork_home` 拿。
9. **P2 审批决策 actor.type 缺省不能默认 'user'**：那是审计造假。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from worker.runtime import net
from worker.runtime.commands import bus
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.handlers import project_io
from worker.runtime.jobs import lease
from worker.runtime.models import Job, JobState
from worker.runtime.providers.ai import cloud as ai_cloud

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture
def reset_net_state() -> Iterator[None]:
    """每个用例前后清 net 的共享 client + 探测缓存，避免跨用例耦合。"""

    async def _reset() -> None:
        await net.aclose_shared_async_client()
        net.reset_proxy_probe_cache()

    asyncio.run(_reset())
    yield
    asyncio.run(_reset())


# ---------------------------------------------------------------------------
# 1 共享 AsyncClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_async_client_is_singleton(
    reset_net_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同进程内二次调用必须拿回同一 httpx.AsyncClient 实例。"""
    monkeypatch.setattr(net, "_configured_proxies", lambda: {})
    a = await net.shared_async_client()
    b = await net.shared_async_client()
    assert a is b, "shared_async_client 每次都新建，连接复用形同虚设"
    assert isinstance(a, httpx.AsyncClient)


@pytest.mark.asyncio
async def test_ai_cloud_reuses_shared_client(
    reset_net_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI provider 不再每次 ``make_async_client``：注入 None 时应拿到 shared
    实例，两次 ``_client_cm`` 拿到的是同一对象。"""
    monkeypatch.setattr(net, "_configured_proxies", lambda: {})
    p = ai_cloud.CloudAIProvider(api_key="sk-x", base_url="https://example.invalid")

    async with p._client_cm() as c1:
        pass
    async with p._client_cm() as c2:
        pass
    assert c1 is c2, "Provider 还在每条请求新建 AsyncClient → TLS 反复握手"


@pytest.mark.asyncio
async def test_tts_cloud_does_not_close_injected_client(
    reset_net_state: None,
) -> None:
    """CloudTTSProvider 借用外部 client 时**不能 aclose** —— 修前
    ``async with client:`` 会关掉共享/注入实例，后续请求全报 client closed。"""
    from worker.runtime.providers.tts import cloud as tts_cloud

    shared = httpx.AsyncClient()

    class _Boom(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:  # noqa: ARG002
            raise httpx.ConnectError("simulated")

    injected = httpx.AsyncClient(transport=_Boom())
    p = tts_cloud.CloudTTSProvider(
        api_key="k",
        base_url="https://example.invalid",
        model="m",
        client=injected,
    )
    with pytest.raises(httpx.ConnectError):
        await p.synthesize("你好")
    # 上面那次调用之后，注入的 client 仍**不能**被关掉
    assert not injected.is_closed, (
        "Provider 关掉了注入的 client：一次失败就让外部实例报废"
    )
    assert not shared.is_closed
    await injected.aclose()
    await shared.aclose()


@pytest.mark.asyncio
async def test_shutdown_closes_shared_client(
    reset_net_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """runtime.shutdown 必须显式 aclose 共享 client，否则 event loop 关得早
    httpx 会刷一堆 "Task was destroyed but pending" 噪音日志。"""
    from worker.runtime.handlers import lifecycle
    from worker.runtime.state import WorkerState

    monkeypatch.setattr(net, "_configured_proxies", lambda: {})
    client = await net.shared_async_client()
    assert not client.is_closed

    state = WorkerState()
    event = asyncio.Event()
    await lifecycle.handle_shutdown({"graceful": True}, state, event)
    assert client.is_closed, "shutdown 没关共享 client"


# ---------------------------------------------------------------------------
# 3 MCP stderr 泵
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_stderr_pump_prevents_deadlock(tmp_path: Path) -> None:
    """子进程狂写 stderr 到 PIPE 缓冲区之上时不再互锁。

    修前：``stderr=PIPE`` 无并发 reader，Server 写满 OS 管道后 ``write`` 阻塞，
    主协程在 stdout 上等 JSON-RPC 响应也阻塞，直到 ``self._timeout``（30s）；
    修后：后台泵持续读 stderr 到环形缓冲，两侧都不阻塞。
    """
    from worker.runtime.agents.mcp_client import McpStdioClient

    # 用一个 Python 子命令：先往 stderr 写 200 KB（远超 Linux 64 KB /
    # Win 4 KB 管道缓冲），再往 stdout 写一条合法的 JSON-RPC 响应。
    # 修前：子进程 write 阻塞在 64KB，主进程 await 30s 超时；
    # 修后：泵把 stderr 抽干，stdout 那一条 response 能正常读到。
    script = tmp_path / "server.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.write('x' * 200_000)\n"
        "sys.stderr.flush()\n"
        'sys.stdout.write(\'' + json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        ) + '\\n\')\n'
        "sys.stdout.flush()\n"
        "import time\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    client = McpStdioClient(["python", str(script)], timeout=5.0)
    async with client:
        # 直接读一个假响应帧 —— 我们要验的是 stderr 没被塞满
        proc = client._proc
        assert proc is not None and proc.stdout is not None
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
        assert line, "泵没起效：子进程写 stderr 时 stdout 那侧永远等不到 response"
    # close 之后 stderr 缓冲里应该有东西（证明泵读过）
    assert len(client._stderr_buf) > 0, "泵没抓到 stderr 内容"


# ---------------------------------------------------------------------------
# 4 lease sweep 一条 UPDATE 完成
# ---------------------------------------------------------------------------


def test_lease_sweep_uses_single_update() -> None:
    """批量 sweep 不该逐行 UPDATE；通过 trace_callback 数 UPDATE 次数。"""
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    repos = Repos(conn)
    repos.workspaces.ensure("ws-l")
    past = "2000-01-01T00:00:00+00:00"
    for i in range(3):
        j = Job(job_type=f"t{i}", payload={}, state=JobState.LEASED)
        repos.jobs.create(j)
        conn.execute(
            "UPDATE jobs SET state=?, lease_expires_at=? WHERE id=?",
            (JobState.LEASED.value, past, j.id),
        )
    conn.commit()

    updates: list[str] = []
    conn.set_trace_callback(lambda sql: updates.append(sql))
    jobs = lease.sweep_expired(conn)
    conn.set_trace_callback(None)

    assert len(jobs) == 3
    # 允许 create 阶段的 UPDATE 混进来 —— 关键判据是 sweep_expired 只走
    # **一条 IN 型 UPDATE**（修前是逐行 UPDATE ... WHERE id=?，即 N 条）
    sweep_updates = [s for s in updates if "update jobs" in s.lower() and "in (" in s.lower()]
    assert len(sweep_updates) == 1, (
        f"sweep_expired 走了 {len(sweep_updates)} 条 IN 型 UPDATE，"
        "应为一条批量 —— 此前逐行 UPDATE 是 N+1"
    )
    row_by_row = [
        s for s in updates
        if "update jobs" in s.lower() and "where id=?" in s.lower().replace(" ", "")
    ]
    assert row_by_row == [], (
        f"sweep_expired 又退回了逐行 UPDATE 形态：{row_by_row}"
    )


# ---------------------------------------------------------------------------
# 5 backup copy 走线程
# ---------------------------------------------------------------------------


def test_backup_uses_to_thread_for_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BackupWorkspace 里 shutil.copy2 + stat 必须在 asyncio.to_thread 里跑。"""

    calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []
    real_to_thread = asyncio.to_thread

    async def spy(fn: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append((fn, args, kwargs))
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr("worker.runtime.handlers.backup.asyncio.to_thread", spy)
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))

    home = tmp_path
    db = home / "stepwork.db"
    conn = sqlite3.connect(str(db))
    run_migrations(conn, _MIG_DIR)
    conn.close()

    async def _run() -> None:
        conn2 = sqlite3.connect(str(db))
        conn2.row_factory = sqlite3.Row
        deps = Deps(repos=Repos(conn2), ingest=None, asr=None, ai=None)
        env = {
            "commandId": "c1", "commandType": "BackupWorkspace", "schemaVersion": "1",
            "actor": {"type": "user", "id": "u"}, "workspaceId": "ws-1",
            "projectId": None, "source": "test",
            "requestedAt": "2026-09-14T00:00:00+00:00",
            "payload": {},
        }
        await bus.dispatch(env, deps)
        conn2.close()

    asyncio.run(_run())
    fn_names = {c[0].__name__ for c in calls if callable(c[0])}
    assert "_do_backup_copy" in fn_names, (
        f"backup 的 copy2 没进 to_thread（实际观测：{fn_names}）—— "
        "GB 级 DB 直接冻结事件循环"
    )


# ---------------------------------------------------------------------------
# 6 project_io 事务 + zip 形状 + 媒体大小上限
# ---------------------------------------------------------------------------


def test_read_bundle_json_rejects_missing(tmp_path: Path) -> None:
    import zipfile as zf

    bundle = tmp_path / "p.zip"
    with zf.ZipFile(bundle, "w") as z:
        z.writestr("manifest.json", "{}")
    with zf.ZipFile(bundle, "r") as z:
        with pytest.raises(bus.DispatchError) as ei:
            project_io._read_bundle_json(z, "project.json", expect=dict)
        assert ei.value.code == "INVALID_ARGUMENT"
        assert "project.json" in ei.value.message


def test_read_bundle_json_rejects_bad_shape(tmp_path: Path) -> None:
    import zipfile as zf

    bundle = tmp_path / "p.zip"
    with zf.ZipFile(bundle, "w") as z:
        # versions.json 是个 dict 而非 list → for v in versions 会崩 TypeError
        z.writestr("versions.json", json.dumps({"oops": "not a list"}))
    with zf.ZipFile(bundle, "r") as z:
        with pytest.raises(bus.DispatchError) as ei:
            project_io._read_bundle_json(z, "versions.json", expect=list)
        assert "must be list" in ei.value.message


def test_read_bundle_json_rejects_list_with_scalars(tmp_path: Path) -> None:
    import zipfile as zf

    bundle = tmp_path / "p.zip"
    with zf.ZipFile(bundle, "w") as z:
        z.writestr("assets.json", json.dumps([1, "str-only", None]))
    with zf.ZipFile(bundle, "r") as z:
        with pytest.raises(bus.DispatchError) as ei:
            project_io._read_bundle_json(z, "assets.json", expect=list)
        assert "must be object" in ei.value.message


def test_media_member_size_cap(tmp_path: Path) -> None:
    """zip bomb 防线：``info.file_size`` 超上限直接拒（不是靠 copy 时才 OOM）。"""
    bundle = tmp_path / "p.zip"
    with zipfile.ZipFile(bundle, "w") as z:
        z.writestr("assets/big.mp4", "b" * 100)
    fake_asset = {
        "id": "a1",
        "kind": "video",
        "local_uri": "file://x",
        "content_hash": "h",
        "created_at": "2026-01-01T00:00:00+00:00",
        "bundle_file": "assets/big.mp4",
    }
    # 把上限临时压到 10 字节，模拟超大成员
    orig = project_io._MAX_MEDIA_MEMBER_BYTES
    project_io._MAX_MEDIA_MEMBER_BYTES = 10
    try:
        with zipfile.ZipFile(bundle, "r") as z:
            with pytest.raises(bus.DispatchError) as ei:
                project_io._restore_asset_file(z, fake_asset, "prj", "a1")
            assert "too large" in ei.value.message
    finally:
        project_io._MAX_MEDIA_MEMBER_BYTES = orig


@pytest.mark.asyncio
async def test_import_transaction_rolls_back_on_conflict(
    tmp_path: Path,
) -> None:
    """4 段 INSERT 中途失败必须整体 rollback。

    构造：先手动插入 ``prj_conflict``；bundle 里也是这个 id，且
    ``remapId=False`` 让 import 不去生成新 id → project INSERT 直接撞主键；
    修前 4 段 INSERT 未包 with conn:，project 抛错后**下一段**已经悄悄
    开了隐式事务，随后 dispatch 出口的 conn.commit() 会把半截项目一起提交。
    """
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    repos = Repos(conn)
    repos.workspaces.ensure("ws-1")
    # 预置 project，等下 import 撞同一 id
    conn.execute(
        "INSERT INTO content_projects (id, workspace_id, title, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        ("prj_conflict", "ws-1", "existing", "active",
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM content_versions").fetchone()[0]

    deps = Deps(repos=repos, ingest=None, asr=None, ai=None)
    bundle = tmp_path / "p.zip"
    with zipfile.ZipFile(bundle, "w") as z:
        z.writestr(
            "project.json",
            json.dumps({
                "id": "prj_conflict", "title": "T", "status": "active",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }),
        )
        z.writestr("assets.json", "[]")
        z.writestr("jobs.json", "[]")
        z.writestr(
            "versions.json",
            json.dumps([
                {
                    "id": "cv_new", "content_type": "script",
                    "content": "hi", "content_hash": "h", "producer": {},
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            ]),
        )

    env = {
        "commandId": "c1", "commandType": "ImportProject", "schemaVersion": "1",
        "actor": {"type": "user", "id": "u"}, "workspaceId": "ws-1",
        "projectId": None, "source": "test",
        "requestedAt": "2026-09-14T00:00:00+00:00",
        "payload": {"bundlePath": str(bundle), "remapId": False},
    }
    result = await bus.dispatch(env, deps)
    assert result["ok"] is False, "主键冲突本该被 dispatch 兜底成 ok=False"

    # 关键：project 的 INSERT 失败之后，versions 里那条 cv_new **不该**残留 ——
    # 修前 4 段 INSERT 未 with conn:，异常路径靠 bus 出口或下条命令的 commit
    # 把半截一起提交。修后 with conn: 的上下文管理器异常自动 rollback。
    leftover = conn.execute(
        "SELECT id FROM content_versions WHERE id = 'cv_new'"
    ).fetchone()
    assert leftover is None, (
        "事务 rollback 未生效：project 插入失败但版本行残留 = 半截项目"
    )
    assert conn.execute("SELECT COUNT(*) FROM content_versions").fetchone()[0] == before


# ---------------------------------------------------------------------------
# 7 import_source content_hash 类型守卫
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_source_rejects_non_str_hash(tmp_path: Path) -> None:
    """content_hash 传 int/dict 一律拒 —— 静默 dedup 会 os.remove 刚下载好的素材。"""
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    repos = Repos(conn)
    repos.workspaces.ensure("ws-1")
    deps = Deps(repos=repos, ingest=None, asr=None, ai=None)
    env = {
        "commandId": "c1", "commandType": "ImportSource", "schemaVersion": "1",
        "actor": {"type": "user", "id": "u"}, "workspaceId": "ws-1",
        "projectId": None, "source": "test",
        "requestedAt": "2026-09-14T00:00:00+00:00",
        "payload": {
            "local_uri": str(tmp_path / "x.mp4"),
            "content_hash": 123,  # 故意传 int
        },
    }
    # bus 的兜底 except 会把 DispatchError 转成 ok=False + 错误消息，
    # 而不是继续 raise —— 断言 result。
    result = await bus.dispatch(env, deps)
    assert result["ok"] is False
    assert "content_hash must be str" in str(result.get("error"))


def test_only_one_stepwork_home_resolver() -> None:
    """历史上 5 个模块各写一份 ``_resolve_stepwork_home``，实现相同但漂移风险高。

    P2 去重：只有 :func:`worker.runtime.cleanup.resolve_stepwork_home` 保留，
    其它 4 处必须走它。这条断言防未来某处"顺手抄一份"回来。

    用 AST 精确定位真实函数定义，避免测试文件自身 docstring 里的字面串误伤。
    """
    import ast

    root = Path("worker/runtime")
    offenders: list[str] = []
    for py in root.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_resolve_stepwork_home":
                offenders.append(f"{py}:{node.lineno}")
    assert not offenders, (
        "又出现了 _resolve_stepwork_home 私有副本，应共享 "
        f"cleanup.resolve_stepwork_home：{offenders}"
    )


# ---------------------------------------------------------------------------
# 9 审批决策 actor.type 缺省不 'user'
# ---------------------------------------------------------------------------


def test_approval_decision_actor_default_is_unknown() -> None:
    """审批决策落点用 ``actor.get('type', 'user')`` 是审计造假 ——
    上游忘传字段时高风险操作被记成"人批的"。修后默认 'unknown'。"""
    from worker.runtime.handlers import approvals

    src = Path(inspect_getfile(approvals)).read_text(encoding="utf-8")
    # 决策那行必须不再出现 'user' 兜底
    assert "actor.get('type', 'user')" not in src and \
           'actor.get("type", "user")' not in src, (
        "审批决策点仍把 actor.type 缺失默认成 'user' —— 审计造假"
    )


def inspect_getfile(mod: Any) -> str:
    import inspect

    return inspect.getfile(mod)
