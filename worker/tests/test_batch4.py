"""第四轮行为锁测试。

覆盖本轮 3 个改动：

1. **配音逐幕并发**：``synthesize_scenes`` 与 ``illustrate_scenes`` 同骨架
   —— ``asyncio.Semaphore + gather``，8 幕 × 5-15s TTS 压到接近 1 幕的时间。
2. **幂等席位原子化**：修前 ``lookup → 执行 → remember`` 三步非原子，
   两条同 key 命令并发都 miss、都执行、都计费；修后 ``reserve`` 撞主键即
   拒收第二条，``release`` 保证失败路径归还席位（``INFLIGHT`` 不会永久卡住）。
3. **``Backup/RestoreWorkspace`` 命名诚实化**：detail 里显式回显
   ``scope=global``，让调用方在协议层就看到"这是全局备份，不是按工作区"。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.commands import bus, idempotency
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.handlers import backup, synthesize_scenes
from worker.runtime.models import CommandEnvelope

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _env_with_key(
    command_type: str,
    key: str,
    workspace_id: str = "ws-1",
    command_id: str = "cmd-1",
) -> CommandEnvelope:
    return CommandEnvelope(
        commandId=command_id,
        commandType=command_type,
        schemaVersion="1",
        actor={"type": "user", "id": "u"},
        workspaceId=workspace_id,
        source="test",
        requestedAt=datetime.now(UTC).isoformat(),
        idempotencyKey=key,
        payload={},
    )


# ---------------------------------------------------------------------------
# 1 synthesize_scenes 并发骨架
# ---------------------------------------------------------------------------


def test_synthesize_scenes_uses_semaphore_and_gather() -> None:
    """源码级断言：并发骨架真在 handler 里，防止下一次重构悄悄退回串行。"""
    src = Path(synthesize_scenes.__file__).read_text(encoding="utf-8")
    assert "asyncio.Semaphore" in src, (
        "SynthesizeScenes 又走回逐幕串行 TTS 了 —— 8 幕 × 5-15s 会白等"
        " 40-120s；必须 Semaphore 卡上限 + gather 并发"
    )
    assert "asyncio.gather" in src, "缺 gather，仍是顺序 await 语义"
    # 关键：start_sec 累加必须在 gather 之后按 idx 顺序跑，不能并发化
    # （并发写 cursor 会撕裂时间轴）
    assert "sorted(" in src and '"idx"' in src, (
        "并发 gather 之后必须按 idx 顺序累加 cursor 计算 start_sec，"
        "否则时间轴会乱"
    )


# ---------------------------------------------------------------------------
# 2 幂等席位 reserve / release
# ---------------------------------------------------------------------------


def _fresh_idem_conn() -> Any:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    return conn


def test_reserve_first_writer_wins_second_rejected() -> None:
    """两条同 key 命令并发跑，第二条必须撞 reserve 失败。"""
    conn = _fresh_idem_conn()
    env_a = _env_with_key("GenerateScript", "dup-key", command_id="A")
    env_b = _env_with_key("GenerateScript", "dup-key", command_id="B")

    assert idempotency.reserve(conn, env_a) is True
    assert idempotency.reserve(conn, env_b) is False, (
        "修前 lookup 都 miss 都放行 → 两条同 key 并发都执行；"
        "修后第二条必须撞主键被拒"
    )


def test_lookup_ignores_inflight_sentinel() -> None:
    """lookup 看到 INFLIGHT 占位不能当结果返回 —— 那份还没生成。"""
    conn = _fresh_idem_conn()
    env = _env_with_key("GenerateScript", "inflight-key")
    idempotency.reserve(conn, env)
    assert idempotency.lookup(conn, env) is None, (
        "INFLIGHT 哨兵不是真结果，lookup 返回它会污染调用方"
    )


def test_release_allows_retry_of_same_key() -> None:
    """第一条失败 release 之后，第二条同 key 必须能重新抢到席位。"""
    conn = _fresh_idem_conn()
    env_a = _env_with_key("GenerateScript", "retry-key", command_id="A")
    env_b = _env_with_key("GenerateScript", "retry-key", command_id="B")

    assert idempotency.reserve(conn, env_a) is True
    idempotency.release(conn, env_a)
    assert idempotency.reserve(conn, env_b) is True, (
        "release 失败 → 同 key 永久被 INFLIGHT 钉住，用户重试无门"
    )


def test_release_only_removes_inflight_not_real_result() -> None:
    """release 不能把已经 remember 的真实结果一起删掉（并发路径下理论上
    不会发生，但代码层面必须防御性只删哨兵）。"""
    conn = _fresh_idem_conn()
    env = _env_with_key("GenerateScript", "keep-key")
    idempotency.reserve(conn, env)
    idempotency.remember(conn, env, {"ok": True, "detail": {"v": 1}})
    idempotency.release(conn, env)
    row = conn.execute(
        "SELECT result_json FROM command_idempotency WHERE idempotency_key=?",
        ("keep-key",),
    ).fetchone()
    assert row is not None, "release 误删了真结果"
    assert row["result_json"] != idempotency._INFLIGHT_SENTINEL


def test_remember_replaces_inflight_with_real_result() -> None:
    """成功路径：remember 覆盖哨兵行成 CommandResult JSON；再 lookup 命中。"""
    conn = _fresh_idem_conn()
    env = _env_with_key("GenerateScript", "flow-key")
    idempotency.reserve(conn, env)
    idempotency.remember(conn, env, {"ok": True, "detail": {"hello": "world"}})
    hit = idempotency.lookup(conn, env)
    assert hit is not None
    assert hit["ok"] is True
    assert hit["detail"]["hello"] == "world"
    # 重放标记必须落进 detail
    assert hit["detail"][idempotency.REPLAY_FLAG] is True


def test_reserve_without_key_is_passthrough() -> None:
    """未启用幂等（无 idempotencyKey）→ 恒 True，绝不阻断业务。"""
    conn = _fresh_idem_conn()
    env = CommandEnvelope(
        commandId="c", commandType="X", schemaVersion="1",
        actor={"type": "user", "id": "u"}, workspaceId="ws-1",
        source="test", requestedAt=datetime.now(UTC).isoformat(),
        payload={},
    )
    assert idempotency.reserve(conn, env) is True


def test_reserve_degrades_open_when_idempotency_table_broken() -> None:
    """幂等表不可用 → 放行（返回 True），与既有 lookup 的降级语义一致；
    幂等只是防重复计费，绝不能反过来卡住业务。"""
    bad_conn = in_memory()  # 没跑迁移，command_idempotency 表根本不存在
    env = _env_with_key("GenerateScript", "any")
    assert idempotency.reserve(bad_conn, env) is True


# ---------------------------------------------------------------------------
# 3 Backup 命名诚实化
# ---------------------------------------------------------------------------


def test_backup_detail_reports_scope_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """命令叫 BackupWorkspace 但动作是全局的 —— detail 必须显式回显
    ``scope="global"``，让调用方在协议层就看到边界。"""
    import sqlite3

    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    db = tmp_path / "stepwork.db"
    conn = sqlite3.connect(str(db))
    run_migrations(conn, _MIG_DIR)
    conn.close()

    from worker.runtime.db.repos import Repos
    from worker.runtime.deps import Deps

    conn2 = sqlite3.connect(str(db))
    conn2.row_factory = sqlite3.Row
    deps = Deps(repos=Repos(conn2), ingest=None, asr=None, ai=None)
    env = {
        "commandId": "c1", "commandType": "BackupWorkspace", "schemaVersion": "1",
        "actor": {"type": "user", "id": "u"}, "workspaceId": "ws-1",
        "projectId": None, "source": "test",
        "requestedAt": datetime.now(UTC).isoformat(),
        "payload": {},
    }
    result = asyncio.run(bus.dispatch(env, deps))
    conn2.close()
    assert result["ok"] is True, result
    assert result["detail"]["scope"] == "global", (
        "Backup/Restore 命名承诺工作区级、实现是全局级 —— "
        "detail 不带 scope=global 就等于把这个 gap 藏在源码 docstring 里，"
        "调用方 / UI 无从感知"
    )


def test_backup_module_docstring_declares_scope_gap() -> None:
    """docstring 里必须明说这是"名字 vs 实现的已知 gap"，否则下一个读者
    还是会当成工作区级用。"""
    src = Path(backup.__file__).read_text(encoding="utf-8")
    assert "全局" in src or "global" in src.lower()
    assert "工作区" in src, (
        "backup 模块 docstring 需要显式讲清"
        "\"名字承诺工作区级 / 实现是全局级\"这条 gap"
    )
