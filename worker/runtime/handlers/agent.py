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

from datetime import UTC, datetime

from worker.runtime.commands.bus import DispatchError
from worker.runtime.db.rows import row_to_dict
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult

_NOTE = "Agent 互操作 V0.2 启用"

#: PRD-AGT-007 连接状态：active 可用 / inactive 停用（该通道调用被拒）
_CONNECTION_STATUSES: frozenset[str] = frozenset({"active", "inactive"})


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
        tasks = [row_to_dict(r) for r in rows]
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"tasks": tasks, "note": _NOTE},
        )

    if env.commandType == "ListAgentArtifacts":
        rows = deps.repos.conn.execute(
            "SELECT * FROM agent_artifacts ORDER BY created_at DESC"
        ).fetchall()
        artifacts = [row_to_dict(r) for r in rows]
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
            detail={"task": row_to_dict(row)},
        )

    if env.commandType == "ListAgentConnections":
        # PRD-AGT-007「Agent Connections 页面：可启停、测试、授权和删除连接」。
        # agent_connections 表 0003 就建好了，此前无任何命令与页面；
        # 现在 agent_record 会为每条协议通道自动建行（见 ensure_connection）。
        rows = deps.repos.conn.execute(
            "SELECT * FROM agent_connections ORDER BY created_at DESC"
        ).fetchall()
        connections = [row_to_dict(r) for r in rows]
        # 任务数一次 GROUP BY 取回，而不是每条连接查一次。连接数少时两者
        # 无差别，但 N+1 是会随数据增长而恶化的写法，没有理由留着。
        counts = {
            str(r["target_agent_id"]): int(r["n"])
            for r in deps.repos.conn.execute(
                "SELECT target_agent_id, COUNT(*) n FROM agent_tasks "
                "GROUP BY target_agent_id"
            ).fetchall()
        }
        for item in connections:
            item["task_count"] = counts.get(str(item["id"]), 0)
        return CommandResult(
            ok=True, commandId=env.commandId, detail={"connections": connections}
        )

    if env.commandType == "SetAgentConnectionStatus":
        # 启停连接（PRD-AGT-007）：停用后该通道的调用会被 bus 拒绝
        payload = env.payload or {}
        conn_id = payload.get("connectionId") or payload.get("connection_id")
        status = str(payload.get("status") or "")
        if not conn_id:
            raise DispatchError("INVALID_ARGUMENT", "connectionId required")
        if status not in _CONNECTION_STATUSES:
            raise DispatchError(
                "INVALID_ARGUMENT",
                f"status must be one of {sorted(_CONNECTION_STATUSES)}",
            )
        cur = deps.repos.conn.execute(
            "UPDATE agent_connections SET status=?, updated_at=? WHERE id=?",
            (status, datetime.now(UTC).isoformat(), str(conn_id)),
        )
        deps.repos.conn.commit()
        if cur.rowcount == 0:
            raise DispatchError("NOT_FOUND", f"connection {conn_id!r} not found")
        row = deps.repos.conn.execute(
            "SELECT * FROM agent_connections WHERE id=?", (str(conn_id),)
        ).fetchone()
        return CommandResult(
            ok=True, commandId=env.commandId, detail={"connection": row_to_dict(row)}
        )

    if env.commandType == "DeleteAgentConnection":
        payload = env.payload or {}
        conn_id = payload.get("connectionId") or payload.get("connection_id")
        if not conn_id:
            raise DispatchError("INVALID_ARGUMENT", "connectionId required")
        cur = deps.repos.conn.execute(
            "DELETE FROM agent_connections WHERE id=?", (str(conn_id),)
        )
        deps.repos.conn.commit()
        if cur.rowcount == 0:
            raise DispatchError("NOT_FOUND", f"connection {conn_id!r} not found")
        # agent_tasks.target_agent_id 有 ON DELETE CASCADE，历史任务随之清理
        return CommandResult(
            ok=True, commandId=env.commandId, detail={"deleted": str(conn_id)}
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by agent handler",
    )
