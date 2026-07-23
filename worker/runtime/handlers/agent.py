"""Agent 只读列表 handler（W8 L.31）。

三个只读命令，全部 ``SELECT`` 不写库：

- ``ListAgentTasks``：列出所有 ``agent_tasks``（按 ``created_at DESC``）。
- ``ListAgentArtifacts``：列出所有 ``agent_artifacts``（按 ``created_at DESC``）。
- ``GetAgentTask``：按 id 取单个 ``agent_task``（兼容 ``payload.taskId`` /
  ``payload.task_id`` 两种命名）。

``agent_tasks`` / ``agent_artifacts`` 两表已在
``migrations/0003_agent_placeholder.sql`` 占位建好，本就为空——W8 L.31
仅落只读列表通路；真实写入路径推 V0.2 Agent 互操作启用时再补
（SYSTEM_SPEC §8.2）。故空表返回空数组 + ``note`` 说明「Agent 互操作 V0.2 启用」。

行转 dict 不做精确字段映射（推 V0.2），用 ``sqlite3.Row`` 列名读取所有列
转普通 dict；TEXT 列 sqlite3 已返回 str，故 ``id`` 等文本列天然是 str。
"""

from __future__ import annotations

from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult

_NOTE = "Agent 互操作 V0.2 启用"


def _row_to_dict(row: Any) -> dict[str, Any]:
    """把 ``sqlite3.Row`` 转普通 dict（所有列名 → 值，原始类型保留）。

    agent_tasks / agent_artifacts 的列在 0003 占位迁移里已定义；W8 L.31
    只做只读列表，不做精确字段映射。TEXT 列 sqlite3 已返回 str，故 ``id``
    等文本列天然是 str；REAL / INTEGER 保留原始数值类型；NULL 保留 None。
    """
    return {key: row[key] for key in row.keys()}


def _resolve_task_id(env: CommandEnvelope) -> str | None:
    """从 payload 解析 taskId（兼容 ``taskId`` / ``task_id`` 两种命名）。"""
    payload = env.payload or {}
    return payload.get("taskId") or payload.get("task_id")


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由 ``ListAgentTasks`` / ``ListAgentArtifacts`` / ``GetAgentTask`` 三个只读命令。"""
    if env.commandType == "ListAgentTasks":
        rows = deps.repos.conn.execute(
            "SELECT * FROM agent_tasks ORDER BY created_at DESC"
        ).fetchall()
        tasks = [_row_to_dict(r) for r in rows]
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"tasks": tasks, "note": _NOTE},
        )

    if env.commandType == "ListAgentArtifacts":
        rows = deps.repos.conn.execute(
            "SELECT * FROM agent_artifacts ORDER BY created_at DESC"
        ).fetchall()
        artifacts = [_row_to_dict(r) for r in rows]
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"artifacts": artifacts, "note": _NOTE},
        )

    if env.commandType == "GetAgentTask":
        tid = _resolve_task_id(env)
        if not tid:
            raise DispatchError("INVALID_ARGUMENT", "missing taskId")
        row = deps.repos.conn.execute(
            "SELECT * FROM agent_tasks WHERE id=?", (tid,)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"agent task {tid!r} not found")
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"task": _row_to_dict(row)},
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by agent handler",
    )
