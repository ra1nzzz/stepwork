"""命令级可观测性（结构化日志 + correlationId + 本地指标）。

改造前 18k 行后端只有 38 处零散日志、0 个指标：用户说「渲染很慢」，我们既
不知道慢在哪一步，也不知道是不是只有他慢。这里锁住的是「出问题时真的查得
出来」——尤其是**失败路径也必须留下记录**，那正是最需要看的那次。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.observability import (
    CommandTimer,
    current_correlation_id,
    resolve_correlation_id,
    summarize,
)

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _db(tmp_path: Path) -> tuple[sqlite3.Connection, Deps]:
    conn = connect(str(tmp_path / "obs.db"))
    run_migrations(conn, _MIG_DIR)
    return conn, Deps(repos=Repos(conn))


def _env(command_type: str, payload: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "commandId": "cid-obs",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "desktop", "id": "ui"},
        "source": "ui",
        "workspaceId": "ws-local",
        "requestedAt": "2026-07-27T00:00:00+00:00",
        "payload": payload or {},
        **extra,
    }


# ---------------------------------------------------------------------------
# correlationId
# ---------------------------------------------------------------------------


def test_upstream_correlation_id_is_honoured() -> None:
    """上游（UI / Rust）带来的 id 要沿用，否则一次操作在两端串不起来。"""
    assert resolve_correlation_id(_env("X", correlationId="trace-42")) == "trace-42"
    assert resolve_correlation_id(_env("X", correlation_id="trace-43")) == "trace-43"


def test_falls_back_to_command_id() -> None:
    """没有 correlationId 就用 commandId —— 它本来就是这次调用的唯一标识，
    比另造一个新 id 更有用（能和审计表对上）。"""
    assert resolve_correlation_id(_env("X")) == "cid-obs"


def test_generates_one_when_nothing_available() -> None:
    got = resolve_correlation_id({})
    assert got and len(got) >= 8


def test_correlation_id_visible_inside_command() -> None:
    """命令执行期间可取到当前 id —— provider 埋在好几层之下，
    逐层传参会污染所有签名且必然有地方忘了传。"""
    assert current_correlation_id() == ""
    with CommandTimer("X", "trace-9") as timer:
        assert current_correlation_id() == "trace-9"
        timer.finish(ok=True, error=None)
    # 出了上下文要还原，不能泄漏到下一条命令
    assert current_correlation_id() == ""


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------


def test_log_line_carries_the_essentials(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO", logger="worker.runtime.command"):
        with CommandTimer("ListProjects", "trace-1", {"keyword": "x"}) as timer:
            timer.finish(ok=True, error=None)
    line = caplog.records[-1].getMessage()
    for token in ("ListProjects", "ok=True", "cid=trace-1", "ms="):
        assert token in line, line


def test_payload_secrets_are_masked(caplog: pytest.LogCaptureFixture) -> None:
    """命令 payload 里可能有 API key —— 日志一律过 §11.3 掩码。"""
    with caplog.at_level("INFO", logger="worker.runtime.command"):
        with CommandTimer("UpdateConfig", "t", {"apiKey": "sk-live-SECRET-1234"}) as timer:
            timer.finish(ok=True, error=None)
    assert "sk-live-SECRET-1234" not in caplog.records[-1].getMessage()


def test_uncaught_exception_still_logs(caplog: pytest.LogCaptureFixture) -> None:
    """异常穿透时也要留一条 —— 那恰恰是最想看到的那次。"""
    with caplog.at_level("INFO", logger="worker.runtime.command"):
        try:
            with CommandTimer("Boom", "t"):
                raise RuntimeError("炸了")
        except RuntimeError:
            pass
    assert "ok=False" in caplog.records[-1].getMessage()


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------


def test_dispatch_records_metrics(tmp_path: Path) -> None:
    import asyncio

    conn, deps = _db(tmp_path)
    try:
        asyncio.run(dispatch(_env("ListProjects", {}), deps))
        rows = conn.execute("SELECT * FROM command_metrics").fetchall()
        assert len(rows) == 1
        assert rows[0]["command_type"] == "ListProjects"
        assert rows[0]["ok"] == 1
        assert rows[0]["duration_ms"] >= 0
        assert rows[0]["correlation_id"] == "cid-obs"
    finally:
        conn.close()


def test_failed_commands_are_recorded_too(tmp_path: Path) -> None:
    """失败也要进指标 —— 只记成功的话失败率恒为 0，等于没这个指标。"""
    import asyncio

    conn, deps = _db(tmp_path)
    try:
        asyncio.run(dispatch(_env("NoSuchCommand", {}), deps))
        row = conn.execute("SELECT * FROM command_metrics").fetchone()
        assert row["ok"] == 0
        assert row["command_type"] == "NoSuchCommand"
    finally:
        conn.close()


def test_error_code_is_stored_without_the_message(tmp_path: Path) -> None:
    """只存错误码前缀：消息里可能带用户内容，而聚合分析只需要分类。"""
    import asyncio

    conn, deps = _db(tmp_path)
    try:
        raw = _env("SchedulePublish", {"variantId": "v", "scheduledAt": "2026-08-01T00:00:00Z"})
        raw["actor"] = {"type": "agent", "id": "evil"}
        raw["source"] = "mcp"
        asyncio.run(dispatch(raw, deps))
        row = conn.execute("SELECT error_code FROM command_metrics").fetchone()
        assert row["error_code"] == "FORBIDDEN_ACTOR"
    finally:
        conn.close()


def test_summarize_aggregates_by_command(tmp_path: Path) -> None:
    import asyncio

    conn, deps = _db(tmp_path)
    try:
        for _ in range(3):
            asyncio.run(dispatch(_env("ListProjects", {}), deps))
        asyncio.run(dispatch(_env("NoSuchCommand", {}), deps))

        rows = {r["command_type"]: r for r in summarize(conn)}
        assert rows["ListProjects"]["count"] == 3
        assert rows["ListProjects"]["failures"] == 0
        assert rows["NoSuchCommand"]["failure_rate"] == 1.0
        assert rows["ListProjects"]["avg_ms"] >= 0
    finally:
        conn.close()


def test_metric_failure_never_breaks_the_command(tmp_path: Path) -> None:
    """指标是观测手段，不该成为新的故障源。"""
    import asyncio

    conn, deps = _db(tmp_path)
    try:
        conn.execute("DROP TABLE command_metrics")
        conn.commit()
        res = asyncio.run(dispatch(_env("ListProjects", {}), deps))
        assert res["ok"] is True
    finally:
        conn.close()
