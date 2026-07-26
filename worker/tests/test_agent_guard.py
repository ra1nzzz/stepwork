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
