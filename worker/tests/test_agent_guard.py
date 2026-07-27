"""PRD §9.1 bus 层 Agent 守卫测试。

守卫的意义是**纵深防御**：MCP 只注册只读工具是第一层，本层保证即便
未来工具被误注册、或 A2A/ACP 适配器复用同一条 dispatch，高风险写命令
对 agent 类调用方依然不可达。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from worker.runtime import ingest
from worker.runtime.commands.bus import (
    _AGENT_ALLOWED_COMMANDS,
    _ROUTES,
    dispatch,
    is_agent_caller,
)
from worker.runtime.commands.envelope import parse_envelope
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _deps() -> Deps:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    return Deps(repos=Repos(c), ingest=ingest)


def _env(
    command_type: str,
    *,
    actor_type: str = "user",
    source: str = "ui",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "commandId": "cmd-guard",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": actor_type, "id": "a1"},
        "source": source,
        "workspaceId": "ws-guard",
        "projectId": None,
        "payload": payload or {},
        "requestedAt": "2026-07-26T00:00:00+00:00",
    }


def test_is_agent_caller_by_actor_type_or_source() -> None:
    assert is_agent_caller(parse_envelope(_env("ListJobs", actor_type="agent")))
    # source 命中即算（即便 actor.type 被伪装成 user）
    assert is_agent_caller(parse_envelope(_env("ListJobs", source="mcp")))
    assert is_agent_caller(parse_envelope(_env("ListJobs", source="a2a")))
    assert is_agent_caller(parse_envelope(_env("ListJobs", source="acp")))
    # 桌面/CLI 用户不是 agent
    assert not is_agent_caller(parse_envelope(_env("ListJobs", source="ui")))
    assert not is_agent_caller(parse_envelope(_env("ListJobs", source="cli")))


@pytest.mark.parametrize(
    "command_type", sorted(set(_ROUTES) - _AGENT_ALLOWED_COMMANDS)
)
async def test_agent_cannot_execute_non_allowlisted_commands(
    command_type: str,
) -> None:
    """默认拒绝：允许清单之外的每一条命令对 agent 都不可达。

    参数化覆盖 _ROUTES 全量，故日后新增任何命令都自动纳入本测试 ——
    这正是黑名单做不到的（黑名单要靠人记得同步）。
    """
    res = await dispatch(_env(command_type, actor_type="agent", source="mcp"), _deps())
    assert res["ok"] is False
    assert "FORBIDDEN_ACTOR" in res["error"]


@pytest.mark.parametrize(
    "command_type", sorted(set(_ROUTES) - _AGENT_ALLOWED_COMMANDS)
)
async def test_guard_triggers_on_source_even_if_actor_masquerades(
    command_type: str,
) -> None:
    """actor.type 伪装成 user 但 source=mcp，仍必须被拦截。"""
    res = await dispatch(_env(command_type, actor_type="user", source="mcp"), _deps())
    assert res["ok"] is False
    assert "FORBIDDEN_ACTOR" in res["error"]


async def test_user_can_still_execute_high_risk_commands() -> None:
    """守卫不得误伤桌面用户：拒绝原因不能是 FORBIDDEN_ACTOR。

    payload 为空会因参数校验失败，但错误码必须是参数类而非权限类——
    证明请求已越过守卫进入 handler。
    """
    res = await dispatch(_env("DeleteAsset", actor_type="user", source="ui"), _deps())
    assert "FORBIDDEN_ACTOR" not in (res.get("error") or "")


async def test_agent_can_still_read_and_analyze() -> None:
    """PRD-AGT-002：外部 Agent 仍可读取项目/查询任务（不在黑名单内）。"""
    res = await dispatch(
        _env("ListProjects", actor_type="agent", source="mcp"), _deps()
    )
    assert res["ok"] is True
    # AnalyzeSource 按 PRD-AGT-002 显式允许（此处无 ai provider 故为 UNAVAILABLE，
    # 但关键是错误不是 FORBIDDEN_ACTOR）
    res2 = await dispatch(
        _env("AnalyzeSource", actor_type="agent", source="mcp", payload={"text": "x"}),
        _deps(),
    )
    assert "FORBIDDEN_ACTOR" not in (res2.get("error") or "")


# ----- PRD-AGT-003：外部 Agent 产出须带来源与信任等级 -----


class _FakeAI:
    name = "fake-ai"
    model = "fake-1"
    estimated_cost_per_1k = 0.0

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "summary": "摘要", "topics": ["a"], "sentiment": "neutral",
            "suggested_title": None, "suggested_tags": [], "key_points": [],
            "target_audience": None, "hook": "钩子", "structure": [], "risks": [],
            "provider": "fake-ai", "model": "fake-1", "confidence": 0.9,
        }


async def test_agent_analysis_recorded_with_trust_level() -> None:
    """MCP 发起的分析：登记 AgentTask + AgentArtifact，带信任等级。"""
    from worker.runtime.db.connection import in_memory
    from worker.runtime.db.migrations import run_migrations
    from worker.runtime.models import ContentProject, Workspace

    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos = Repos(c)
    ws = repos.workspaces.insert(Workspace(name="ws-a", root_path="/tmp/a"))
    prj = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    deps = Deps(repos=repos, ingest=ingest, ai=_FakeAI())

    env = _env("AnalyzeSource", actor_type="agent", source="mcp",
               payload={"text": "外部 Agent 请求分析"})
    env["workspaceId"] = ws
    env["projectId"] = prj
    res = await dispatch(env, deps)
    assert res["ok"] is True, res.get("error")

    task = c.execute("SELECT * FROM agent_tasks").fetchone()
    assert task is not None, "外部 Agent 调用必须登记 AgentTask"
    assert task["task_type"] == "AnalyzeSource"
    assert task["initiator"].startswith("agent:")
    assert task["state"] == "succeeded"

    art = c.execute("SELECT * FROM agent_artifacts").fetchone()
    assert art is not None, "外部产出必须登记 AgentArtifact"
    # 验收标准的核心：来源 + 信任等级
    assert art["trust_level"] == "external-unverified"
    assert art["review_state"] == "pending_review"
    assert art["agent_task_id"] == task["id"]
    assert art["producer_agent_id"] == "conn_mcp"


async def test_user_command_not_recorded_as_agent_activity() -> None:
    """桌面用户自己的操作不应被记成外部 Agent 活动。"""
    from worker.runtime.db.connection import in_memory
    from worker.runtime.db.migrations import run_migrations
    from worker.runtime.models import ContentProject, Workspace

    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos = Repos(c)
    ws = repos.workspaces.insert(Workspace(name="ws-u", root_path="/tmp/u"))
    prj = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    deps = Deps(repos=repos, ingest=ingest, ai=_FakeAI())

    env = _env("AnalyzeSource", actor_type="user", source="ui",
               payload={"text": "用户自己分析"})
    env["workspaceId"] = ws
    env["projectId"] = prj
    res = await dispatch(env, deps)
    assert res["ok"] is True

    assert c.execute("SELECT COUNT(*) n FROM agent_tasks").fetchone()["n"] == 0
    assert c.execute("SELECT COUNT(*) n FROM agent_artifacts").fetchone()["n"] == 0


async def test_agent_artifact_recorded_without_project_id_in_envelope() -> None:
    """回归（R3）：MCP 信封**不带 projectId**（见 mcp/server.py），
    handler 内部才落到默认项目。此前 agent_record 只读 env.projectId，
    导致 agent_artifacts 因 NOT NULL + FK 永远写不进去 —— AGT-003 名义
    达成、实际是空表。现应从产物反查项目。
    """
    from worker.runtime.db.connection import in_memory
    from worker.runtime.db.migrations import run_migrations

    c = in_memory()
    run_migrations(c, _MIG_DIR)
    deps = Deps(repos=Repos(c), ingest=ingest, ai=_FakeAI())

    env = _env("AnalyzeSource", actor_type="agent", source="mcp",
               payload={"text": "MCP 请求分析"})
    env["projectId"] = None  # 与真实 MCP 一致
    res = await dispatch(env, deps)
    assert res["ok"] is True, res.get("error")

    art = c.execute("SELECT * FROM agent_artifacts").fetchone()
    assert art is not None, "MCP 路径下外部产物必须登记（此前恒为空表）"
    assert art["trust_level"] == "external-unverified"
    # 项目从产物反查得到，与 content_versions 一致
    cv = c.execute(
        "SELECT project_id FROM content_versions WHERE id=?",
        (res["artifact_ids"][0],),
    ).fetchone()
    assert art["project_id"] == cv["project_id"]


async def test_pure_read_by_agent_not_recorded_as_task() -> None:
    """纯读（List*/Get* 且无产物）不登记，避免读噪声撑大 agent_tasks。"""
    from worker.runtime.db.connection import in_memory
    from worker.runtime.db.migrations import run_migrations

    c = in_memory()
    run_migrations(c, _MIG_DIR)
    deps = Deps(repos=Repos(c), ingest=ingest)

    res = await dispatch(
        _env("ListProjects", actor_type="agent", source="mcp"), deps
    )
    assert res["ok"] is True
    assert c.execute("SELECT COUNT(*) n FROM agent_tasks").fetchone()["n"] == 0


def test_mcp_tool_commands_subset_of_allowlist() -> None:
    """MCP 暴露的每个工具都必须在 bus 允许清单内。

    两层防线必须自洽：若 MCP 注册了清单外的工具，该工具在运行时会被 bus
    拒绝（用户看到功能坏掉）；反过来若清单放开了 MCP 没暴露的写命令，
    则是无谓的权限面。本测试让两层的错配在 CI 就暴露。
    """
    from mcp.server import _TOOL_COMMANDS

    exposed = set(_TOOL_COMMANDS.values())
    assert exposed, "MCP 工具表为空（导入路径变了？）"
    assert not exposed - _AGENT_ALLOWED_COMMANDS, (
        f"MCP 暴露了允许清单之外的命令: {sorted(exposed - _AGENT_ALLOWED_COMMANDS)}"
    )


# ----- PRD-AGT-007：停用的连接必须真的拒绝调用 -----


async def test_disabled_connection_blocks_even_allowed_commands() -> None:
    """停用通道后连只读命令也不给——否则「停用」只是 UI 装饰。"""
    from worker.runtime.db.connection import in_memory
    from worker.runtime.db.migrations import run_migrations

    c = in_memory()
    run_migrations(c, _MIG_DIR)
    deps = Deps(repos=Repos(c), ingest=ingest)

    # 先正常调一次（会自动建连接行）
    ok_first = await dispatch(
        _env("ListProjects", actor_type="agent", source="mcp"), deps
    )
    assert ok_first["ok"] is True

    # 停用该通道
    disabled = await dispatch(
        _env(
            "SetAgentConnectionStatus",
            actor_type="user",
            source="ui",
            payload={"connectionId": "conn_mcp", "status": "inactive"},
        ),
        deps,
    )
    assert disabled["ok"] is True, disabled.get("error")

    blocked = await dispatch(
        _env("ListProjects", actor_type="agent", source="mcp"), deps
    )
    assert blocked["ok"] is False
    assert "已被停用" in blocked["error"]

    # 重新启用后恢复
    await dispatch(
        _env(
            "SetAgentConnectionStatus",
            actor_type="user",
            source="ui",
            payload={"connectionId": "conn_mcp", "status": "active"},
        ),
        deps,
    )
    restored = await dispatch(
        _env("ListProjects", actor_type="agent", source="mcp"), deps
    )
    assert restored["ok"] is True


async def test_missing_connection_row_does_not_block() -> None:
    """首次调用时连接行还不存在，不能因此拒绝。"""
    from worker.runtime.db.connection import in_memory
    from worker.runtime.db.migrations import run_migrations

    c = in_memory()
    run_migrations(c, _MIG_DIR)
    deps = Deps(repos=Repos(c), ingest=ingest)
    res = await dispatch(_env("ListProjects", actor_type="agent", source="mcp"), deps)
    assert res["ok"] is True
