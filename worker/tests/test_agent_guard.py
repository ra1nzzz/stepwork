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
    _AGENT_FORBIDDEN_COMMANDS,
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


@pytest.mark.parametrize("command_type", sorted(_AGENT_FORBIDDEN_COMMANDS))
async def test_agent_cannot_execute_high_risk_commands(command_type: str) -> None:
    """§9.1 全部高风险命令对 agent 一律拒绝（不依赖 payload 合法性）。"""
    res = await dispatch(_env(command_type, actor_type="agent", source="mcp"), _deps())
    assert res["ok"] is False
    assert "FORBIDDEN_ACTOR" in res["error"]


@pytest.mark.parametrize("command_type", sorted(_AGENT_FORBIDDEN_COMMANDS))
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
