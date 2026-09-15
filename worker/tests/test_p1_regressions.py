"""P1 高影响修复的行为锁测试。

覆盖 Review 报告里被判定为 P1 但已经具备生产损害或数据完整性风险的 4 项：

1. ``publish_common._authorization_error`` **fail-closed** —— 授权 payload
   损坏 / 缺 ``content_hash`` 时静默放行 = 篡改载荷绕过授权。
2. ``commands.bus.dispatch`` **未预期异常必须记 traceback** —— 此前
   response 只剩 200 字截断字符串，``worker.log`` 里完全没栈。
3. ``db.repos._safe_json`` **一行坏 JSON 不能毁掉整个 workspace** ——
   此前 5 处裸 ``json.loads`` 让 ensure/get 抛 JSONDecodeError，
   lifecycle 每个 content 命令入口都调它，全 workspace 命令连锁失效。
4. ``cleanup.db_retention_sweep`` 三表按策略清 —— 之前每行幂等缓存整段
   CommandResult JSON 永不淘汰，DB 只单调膨胀。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from worker.runtime import cleanup
from worker.runtime.commands import bus
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.handlers import publish_common
from worker.runtime.models import Job, JobState

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _fresh_conn() -> Any:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    return conn


# ---------------------------------------------------------------------------
# P1-A  publish 授权 fail-closed
# ---------------------------------------------------------------------------


def _conn_with_approval(payload_text: str) -> Any:
    conn = _fresh_conn()
    conn.execute(
        "INSERT INTO approval_requests "
        "(id, actor, action_type, target, payload, status, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            "apr-1",
            "human/u1",
            "publish",
            "ver-1",
            payload_text,
            "approved",
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    return conn


def test_authorization_missing_content_hash_is_rejected() -> None:
    """payload 缺 content_hash → 拒绝（不能"没有哈希就不校验"）。"""
    conn = _conn_with_approval(json.dumps({"note": "legacy, no hash"}))
    msg = publish_common._authorization_error(
        conn, "apr-1", {"versionId": "ver-1"}
    )
    assert msg is not None, (
        "content_hash 缺失时静默放行 = 攻击者把 payload 打坏就能绕过哈希校验"
    )
    assert "content_hash" in msg


def test_authorization_payload_corrupt_is_rejected() -> None:
    """payload 是非法 JSON 时同样 fail-closed（旧行为 bound={} 会短路）。"""
    conn = _conn_with_approval("{not json")
    msg = publish_common._authorization_error(
        conn, "apr-1", {"versionId": "ver-1"}
    )
    assert msg is not None


def test_authorization_wrong_hash_still_rejected() -> None:
    """哈希不匹配依然拒绝（保持既有语义）。"""
    conn = _conn_with_approval(json.dumps({"content_hash": "different-hash"}))
    msg = publish_common._authorization_error(
        conn, "apr-1", {"versionId": "ver-1"}
    )
    assert msg is not None
    assert "changed" in msg or "content_hash" in msg


# ---------------------------------------------------------------------------
# P1-B  bus 未预期异常必须落 traceback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_unexpected_exception_logs_traceback(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """兜底 except 除了把错误转成干净的 ok=False，还必须 logger.exception。

    此前 worker.log 里完全没有 traceback —— ``observability.py`` 开篇自陈
    「出问题基本靠猜」就是这个洞。
    """
    deps = Deps(repos=Repos(_fresh_conn()), ingest=None, asr=None, ai=None)

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("handler exploded internally")

    # 通过 import_module 打桩，绕开真实 handler 模块
    class _FakeMod:
        handle = staticmethod(_boom)

    monkeypatch.setattr(
        "importlib.import_module",
        lambda _path, *_a, **_k: _FakeMod,
    )
    env = {
        "commandId": "cmd-1",
        "commandType": "ListProjects",
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "workspaceId": "ws-1",
        "projectId": None,
        "source": "test",
        "requestedAt": datetime.now(UTC).isoformat(),
        "payload": {},
    }
    with caplog.at_level(logging.ERROR, logger="worker.runtime"):
        result = await bus.dispatch(env, deps)

    assert result["ok"] is False
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("handler exploded internally" in r.getMessage() for r in errors), (
        "未预期异常没进日志 —— 修前 response 只剩 str(exc)，"
        "worker.log 完全没栈"
    )
    assert any(r.exc_info is not None for r in errors), (
        "logger.exception 才会带 exc_info；仅 logger.error 不算修复到位"
    )


# ---------------------------------------------------------------------------
# P1-C  DB repos JSON 崩溃防护
# ---------------------------------------------------------------------------


def test_workspace_with_corrupt_settings_still_loads(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``workspaces.settings`` 一行坏 JSON 以前会让
    ``WorkspaceRepo.get`` 抛 ``JSONDecodeError``；lifecycle 每条命令入口
    都调它 → 该 workspace 所有命令全挂。修后：降级为 ``{}`` + warning。"""
    conn = _fresh_conn()
    repos = Repos(conn)
    ws = repos.workspaces.ensure("ws-corrupt")
    conn.execute(
        "UPDATE workspaces SET settings = ? WHERE id = ?",
        ("{not json", ws.id),
    )
    conn.commit()

    with caplog.at_level(logging.WARNING, logger="worker.runtime.db.repos"):
        reloaded = repos.workspaces.ensure(ws.id)

    assert reloaded is not None
    assert reloaded.settings == {}, "损坏 JSON 应降级到空 dict"
    assert any(
        "corrupt JSON" in r.getMessage() for r in caplog.records
    ), "降级应留一行 warning 便于 grep"


def test_job_with_corrupt_payload_still_loads() -> None:
    """jobs.payload 同上一条契约 —— 独立覆盖避免将来某处回归时其它掩盖它。"""
    conn = _fresh_conn()
    repos = Repos(conn)
    repos.workspaces.ensure("ws-j")
    job = Job(job_type="render", payload={"a": 1}, state=JobState.PENDING)
    repos.jobs.create(job)
    conn.execute("UPDATE jobs SET payload = ? WHERE id = ?", ("bad{", job.id))
    conn.commit()

    reloaded = repos.jobs.get(job.id)
    assert reloaded is not None
    assert reloaded.payload == {}


# ---------------------------------------------------------------------------
# P1-D  retention_sweep 清 DB 三表
# ---------------------------------------------------------------------------


def _seed_idempotency(conn: Any, key: str, created_at: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO command_idempotency "
        "(workspace_id, command_type, idempotency_key, result_json, "
        "command_id, created_at) VALUES (?,?,?,?,?,?)",
        ("ws-1", "GenerateScript", key, "{}", f"cmd-{key}", created_at),
    )
    conn.commit()


def test_db_retention_sweep_scheduled_cleans_old_rows() -> None:
    """scheduled 模式清早于 retentionDays 的 idempotency / metrics 行。"""
    conn = _fresh_conn()
    now = datetime.now(UTC)
    _seed_idempotency(conn, "old", (now - timedelta(days=30)).isoformat())
    _seed_idempotency(conn, "fresh", (now - timedelta(days=1)).isoformat())

    removed = cleanup.db_retention_sweep(conn, retention_days=7, mode="scheduled")
    assert removed >= 1
    remaining = conn.execute(
        "SELECT idempotency_key FROM command_idempotency"
    ).fetchall()
    keys = {r["idempotency_key"] for r in remaining}
    assert "old" not in keys, "30 天前的幂等缓存应被清扫"
    assert "fresh" in keys, "1 天内的幂等缓存必须保留"


def test_db_retention_sweep_manual_is_noop() -> None:
    """manual 模式下不动 —— 与文件清扫策略一致。"""
    conn = _fresh_conn()
    _seed_idempotency(conn, "anytime", "2000-01-01T00:00:00+00:00")
    removed = cleanup.db_retention_sweep(conn, retention_days=7, mode="manual")
    assert removed == 0
    rows = conn.execute("SELECT 1 FROM command_idempotency").fetchall()
    assert len(rows) == 1


def test_db_retention_sweep_immediate_clears_all() -> None:
    conn = _fresh_conn()
    _seed_idempotency(conn, "recent", datetime.now(UTC).isoformat())
    _seed_idempotency(conn, "old", "2000-01-01T00:00:00+00:00")
    removed = cleanup.db_retention_sweep(conn, retention_days=7, mode="immediate")
    assert removed == 2
    assert conn.execute("SELECT 1 FROM command_idempotency").fetchall() == []


def test_db_retention_sweep_tolerates_missing_tables() -> None:
    """迁移未到 0009 / 0011 就跑清理也不能崩启动 —— 视作无操作。"""
    import sqlite3

    bare = sqlite3.connect(":memory:")
    removed = cleanup.db_retention_sweep(bare, retention_days=7, mode="scheduled")
    assert removed == 0


def test_db_retention_sweep_keeps_recent_audit() -> None:
    """审计事件按 2×retentionDays 且下限 30 天保留，防止刚审完就被抹。"""
    conn = _fresh_conn()
    now = datetime.now(UTC)
    conn.execute(
        "INSERT INTO audit_events "
        "(id, actor, source_protocol, command, timestamp) VALUES (?,?,?,?,?)",
        ("aud-1", "human/u", "cli", "Test", (now - timedelta(days=10)).isoformat()),
    )
    conn.execute(
        "INSERT INTO audit_events "
        "(id, actor, source_protocol, command, timestamp) VALUES (?,?,?,?,?)",
        ("aud-2", "human/u", "cli", "Test", (now - timedelta(days=60)).isoformat()),
    )
    conn.commit()

    cleanup.db_retention_sweep(conn, retention_days=7, mode="scheduled")
    ids = {r["id"] for r in conn.execute("SELECT id FROM audit_events").fetchall()}
    assert "aud-1" in ids, "10 天前的审计在 30 天下限内应保留"
    assert "aud-2" not in ids, "60 天前的审计超过 30 天下限应被清"
