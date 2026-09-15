"""第七轮：双组合根合一（build_deps 单一装配入口）行为锁。

对应 yt-dev-review 归档里 dimension C P1 #1 —— 两条 ``Deps`` 装配路径
（Rust sidecar ``handlers.commands.handle_command`` 与 CLI/MCP 门面
``worker.runtime.app.run_command``）字段不一致、Provider 缓存绕开：

- app.py 用散装 ``resolve_*`` → 每条命令重建 6 个 Provider，Whisper
  模型反复重载、``shutil.which`` 反复扫盘；
- app.py 不注 ``notify`` → CLI/MCP 路径下 9 个 handler 的
  ``deps.notify`` 静默 no-op（长任务无进度）；
- app.py 不注 ``worker_state`` → ``RestoreWorkspace`` 里
  ``if deps.worker_state is not None: ...`` 被静默跳过，恢复后的
  ``state.db_conn`` 不回写；下一次 dispatch 又拿旧 conn。

收口到 :func:`worker.runtime.deps.build_deps` 后，两条路径同源。本
文件用**结构 + 语义**双锁死：
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from worker.runtime.deps import build_deps
from worker.runtime.state import WorkerState

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"

# ---------------------------------------------------------------------------
# 单一装配入口存在
# ---------------------------------------------------------------------------


def test_build_deps_is_the_only_public_composer() -> None:
    """两条装配路径都走 build_deps；生产代码里**不应**再有直接
    ``Deps(...)`` 构造 —— 那是漂移的起点。"""
    import ast
    from pathlib import Path

    root = Path("worker/runtime")
    offenders: list[str] = []
    for py in list(root.rglob("*.py")):
        # 测试文件里造 Deps 是合理的（fixture）；本目录只扫生产代码
        if "test_" in py.name:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Deps"
            ):
                # deps.py 自身里 build_deps 会 new 一个 Deps，那是唯一合法点
                if py.name == "deps.py":
                    continue
                offenders.append(f"{py}:{node.lineno}")
    assert offenders == [], (
        f"生产代码又出现了 build_deps 之外的 Deps(...) 构造：{offenders} —— "
        "把装配收口到 build_deps 是防漂移的地基"
    )


# ---------------------------------------------------------------------------
# 语义：notify / worker_state 必须注入
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_deps_injects_notify_and_worker_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """修前 CLI/MCP 路径漏注 notify + worker_state，长任务无进度 +
    Restore 不回写；修后两条路径都必须有。"""
    from worker.runtime.db.connection import in_memory
    from worker.runtime.db.migrations import run_migrations

    conn = in_memory()
    run_migrations(conn, _MIG_DIR)

    async def _notify(_method: str, _params: dict[str, Any]) -> None:
        return None

    state = WorkerState()
    state.db_conn = conn
    state.notify = _notify

    # 避免真加载 Provider（CI 无 whisper / ffmpeg）；build_deps 用
    # asyncio.to_thread 包同步 get_provider_bundle，替换函数也必须是 sync
    def _fake_bundle(_ws: str | None) -> dict[str, Any]:
        return {
            "asr": None, "ai": None, "tts": None, "image": None,
            "renderer": None, "scene_detector": None,
        }

    monkeypatch.setattr(
        "worker.runtime.providers.resolve.get_provider_bundle", _fake_bundle
    )
    deps = await build_deps(state, "ws-1")

    assert deps.notify is _notify, "build_deps 必须把 state.notify 传到 Deps"
    assert deps.worker_state is state, "build_deps 必须把 state 传给 Deps"
    assert deps.repos.conn is conn
    assert deps.ingest is not None


@pytest.mark.asyncio
async def test_build_deps_requires_bootstrapped_conn() -> None:
    """未 bootstrap 的 state.db_conn is None 是明确的调用方 bug —— 立刻
    RuntimeError，而不是让 dispatch 里第一次 repos.workspaces.ensure 崩
    AttributeError 后又被 bus 兜底转 internal:。"""
    state = WorkerState()
    state.db_conn = None
    with pytest.raises(RuntimeError, match="bootstrap"):
        await build_deps(state, "ws-1")


@pytest.mark.asyncio
async def test_build_deps_uses_provider_bundle_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """装配走 ``get_provider_bundle``（进程级缓存）而不是散装 ``resolve_*``
    —— 这是修前 app.py 与 handlers/commands.py 的核心漂移点。"""
    from worker.runtime.db.connection import in_memory
    from worker.runtime.db.migrations import run_migrations

    conn = in_memory()
    run_migrations(conn, _MIG_DIR)

    calls: list[str | None] = []

    def fake_bundle(ws_id: str | None) -> dict[str, Any]:
        calls.append(ws_id)
        return {
            "asr": None, "ai": None, "tts": None, "image": None,
            "renderer": None, "scene_detector": None,
        }

    monkeypatch.setattr(
        "worker.runtime.providers.resolve.get_provider_bundle", fake_bundle
    )
    state = WorkerState()
    state.db_conn = conn

    await build_deps(state, "ws-target")
    await build_deps(state, "ws-target")
    assert calls == ["ws-target", "ws-target"], (
        "装配没走 get_provider_bundle —— 每条命令都会重新扫盘 / 重载模型"
    )


def test_app_and_handler_paths_use_the_same_composer() -> None:
    """两条生产入口的源码都必须 import 并使用 build_deps，防未来某次
    重构悄悄退回散装装配。"""
    import inspect

    from worker.runtime import app as app_mod
    from worker.runtime.handlers import commands as cmds_mod

    for mod in (app_mod, cmds_mod):
        src = inspect.getsource(mod)
        assert "build_deps" in src, (
            f"{mod.__name__} 装配没走 build_deps —— "
            "双组合根漂移又回来了"
        )
        assert "resolve_ai(" not in src and "resolve_asr(" not in src, (
            f"{mod.__name__} 里又出现了散装 resolve_* 调用，绕开了缓存"
        )
