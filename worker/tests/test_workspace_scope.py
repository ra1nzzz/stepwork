"""第五轮：跨 workspace 审计/审批泄露修复的行为锁测试。

对应 yt-dev-review 里 dimension A 的 P1 #4：

- ``audit_events`` 表此前**根本没有 workspace_id 列**，
  ``maintenance.ListAuditEvents`` 与 ``approvals.ListApprovalRequests``
  两条 list 命令都无 workspace 过滤；而 ListAuditEvents 在 bus 的
  ``_AGENT_ALLOWED_COMMANDS`` 允许清单里 → 外部 MCP Agent 可读到
  全库审计事件与审批请求（provider_invocation payload 里就有
  provider / model / cost 这类商业信息）。

修后：
- 新迁移 0015 给两张表各加 ``workspace_id`` + 复合索引；
- 写侧（``audit.record_provider_invocation`` / ``record_event`` /
  ``approvals.create_request`` 及 3 个调用点）都带上 env.workspaceId；
- 读侧默认按 env.workspaceId 过滤，NULL 老数据当"归属未知"漏掉，
  宁可少看不多看。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from worker.runtime import audit
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.handlers import approvals as approvals_mod

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _env(
    command_type: str,
    payload: dict[str, Any],
    workspace_id: str,
    command_id: str = "cmd-x",
    actor_type: str = "user",
) -> dict[str, Any]:
    return {
        "commandId": command_id,
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": actor_type, "id": "a"},
        "workspaceId": workspace_id,
        "projectId": None,
        "source": "test",
        "requestedAt": datetime.now(UTC).isoformat(),
        "payload": payload,
    }


def _setup() -> tuple[Deps, str]:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    repos = Repos(conn)
    ws = "ws-A"
    repos.workspaces.ensure(ws)
    return Deps(repos=repos, ingest=None, asr=None, ai=None), ws


# ---------------------------------------------------------------------------
# 迁移 0015 结构
# ---------------------------------------------------------------------------


def test_migration_0015_adds_workspace_id_columns() -> None:
    """新库必须两张表都带 workspace_id 列（迁移 0015），否则过滤无从谈起。"""
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    for table in ("audit_events", "approval_requests"):
        cols = {
            row[1] for row in conn.execute(
                f"PRAGMA table_info({table})"  # noqa: S608
            ).fetchall()
        }
        assert "workspace_id" in cols, (
            f"{table} 缺 workspace_id 列 —— 迁移 0015 未生效"
        )


# ---------------------------------------------------------------------------
# 写侧
# ---------------------------------------------------------------------------


def test_audit_record_event_writes_workspace_id() -> None:
    """record_event 落库必须带 env.workspaceId，否则读侧过滤会漏掉新行。"""
    deps, ws = _setup()
    conn = deps.repos.conn
    env = _env("SaveScript", {}, ws)
    from worker.runtime.commands.envelope import parse_envelope
    parsed = parse_envelope(env)
    audit.record_event(conn, parsed, audit.EVENT_SCRIPT_SAVED, {"k": 1})
    row = conn.execute(
        "SELECT workspace_id FROM audit_events WHERE event_type=?",
        (audit.EVENT_SCRIPT_SAVED,),
    ).fetchone()
    assert row is not None
    assert row["workspace_id"] == ws, (
        "record_event 没写 workspace_id → 读侧默认过滤会把新行也漏掉"
    )


def test_audit_record_provider_invocation_writes_workspace_id() -> None:
    deps, ws = _setup()
    conn = deps.repos.conn
    env = _env("AnalyzeSource", {}, ws)
    from worker.runtime.commands.envelope import parse_envelope
    parsed = parse_envelope(env)
    audit.record_provider_invocation(
        conn, parsed, {"provider": "openai", "model": "m", "estimated_cost": 0.5}
    )
    row = conn.execute(
        "SELECT workspace_id FROM audit_events WHERE event_type='provider_invocation'"
    ).fetchone()
    assert row is not None and row["workspace_id"] == ws


def test_approvals_create_request_writes_workspace_id() -> None:
    rid = None
    deps, ws = _setup()
    rid = approvals_mod.create_request(
        deps.repos.conn,
        actor="user:u",
        action_type="GenerateScript",
        target="prj-1",
        workspace_id=ws,
    )
    row = deps.repos.conn.execute(
        "SELECT workspace_id FROM approval_requests WHERE id=?", (rid,)
    ).fetchone()
    assert row is not None and row["workspace_id"] == ws


# ---------------------------------------------------------------------------
# 读侧过滤（核心安全语义）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_audit_events_is_workspace_scoped() -> None:
    """A 工作区里的 ListAuditEvents 绝不能读到 B 工作区的审计事件 ——
    修前的跨 workspace 泄露正是这个方向。"""
    deps, ws_a = _setup()
    ws_b = "ws-B"
    deps.repos.workspaces.ensure(ws_b)
    conn = deps.repos.conn
    from worker.runtime.commands.envelope import parse_envelope

    for ws in (ws_a, ws_b):
        env = _env("AnalyzeSource", {}, ws)
        audit.record_event(
            conn, parse_envelope(env), audit.EVENT_PROJECT_CREATED,
            {"secret_from": ws},
        )

    res = await dispatch(_env("ListAuditEvents", {}, ws_a), deps)
    assert res["ok"] is True
    events = res["detail"]["events"]
    # 只能看见 A 的行；B 的那条绝不能出现在结果里
    assert len(events) == 1, f"workspace 过滤未生效，读到 {len(events)} 条"
    assert events[0]["workspace_id" if "workspace_id" in events[0] else "command"]
    # 更硬的判据：payload 里没有 ws-B 的痕迹
    for ev in events:
        assert "ws-B" not in json.dumps(ev, ensure_ascii=False), (
            f"跨 workspace 泄露：读到 ws-B 的行 {ev}"
        )


@pytest.mark.asyncio
async def test_list_audit_events_hides_legacy_null_rows() -> None:
    """迁移前落库的老行 workspace_id 是 NULL —— 归属未知，宁可少看不多看。"""
    deps, ws = _setup()
    conn = deps.repos.conn
    conn.execute(
        "INSERT INTO audit_events (id, actor, source_protocol, command, "
        "timestamp, event_type, payload, workspace_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "legacy-1", "user:x", "cli", "Old",
            datetime.now(UTC).isoformat(), "provider_invocation", "{}",
            None,  # 老数据：归属未知
        ),
    )
    conn.commit()
    res = await dispatch(_env("ListAuditEvents", {}, ws), deps)
    assert res["ok"] is True
    assert res["detail"]["events"] == [], (
        "NULL workspace_id 被读到 = 只要用户 workspace 拼错也能撞库"
    )


@pytest.mark.asyncio
async def test_list_approval_requests_is_workspace_scoped() -> None:
    """ListApprovalRequests 也在 _AGENT_ALLOWED_COMMANDS 里 —— 同样必须
    按 workspace 过滤。"""
    deps, ws_a = _setup()
    ws_b = "ws-B"
    deps.repos.workspaces.ensure(ws_b)
    for ws in (ws_a, ws_b):
        approvals_mod.create_request(
            deps.repos.conn,
            actor="user:u",
            action_type="GenerateScript",
            target=f"prj-{ws}",
            workspace_id=ws,
        )

    res = await dispatch(_env("ListApprovalRequests", {}, ws_a), deps)
    assert res["ok"] is True
    items = res["detail"]["approvals"]
    assert len(items) == 1, f"workspace 过滤未生效，读到 {len(items)} 条审批"
    for it in items:
        assert "ws-B" not in json.dumps(it, ensure_ascii=False)


@pytest.mark.asyncio
async def test_agent_caller_cannot_read_other_workspace_audit() -> None:
    """核心威胁模型：外部 MCP Agent 通过 ListAuditEvents 也拿不到别人的
    workspace 数据 —— bus 的允许清单允许这个命令，但读侧过滤是第二道防线。"""
    deps, ws_victim = _setup()
    conn = deps.repos.conn
    from worker.runtime.commands.envelope import parse_envelope
    victim_env = _env("AnalyzeSource", {}, ws_victim)
    audit.record_event(
        conn, parse_envelope(victim_env), audit.EVENT_SCRIPT_SAVED,
        {"private": "content-of-victim"},
    )

    # Agent 用一个不同的 workspaceId 发命令
    attacker_env = _env(
        "ListAuditEvents", {}, "ws-attacker", actor_type="agent"
    )
    res = await dispatch(attacker_env, deps)
    assert res["ok"] is True
    assert "content-of-victim" not in json.dumps(res, ensure_ascii=False), (
        "Agent 通过 ListAuditEvents 读到了别的 workspace 的审计 payload"
    )
