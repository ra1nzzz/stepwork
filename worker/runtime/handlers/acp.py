"""ACP 命令（PRD-AGT-006：启动本地 Agent、展示流式状态与权限请求）。

命令：

- ``AddAcpAgent``：登记一个本地 Agent（启动命令），登记即探测。
- ``StartAcpSession``：建会话并**绑定项目**，Root/Scope 锁到项目目录。
- ``SendAcpPrompt``：发一轮提示；流式更新经 ``deps.notify`` 实时回推 UI，
  Agent 的权限请求转成 Approval Center 的待批项。
- ``EndAcpSession``：结束会话并关子进程。

三条 §13.6 约束在这里落地：

1. **Session 绑定 Project**（写 ``agent_sessions``，FK 指向项目）；
2. **Agent 不直接读整个 Workspace** —— ``cwd`` 只给项目自己的素材目录；
3. **权限请求必须过人** —— Agent 要做危险动作时，我们不自动放行，而是
   落 Approval Center 等人决定（PRD-AGT-008）。当前实现取**默认拒绝**：
   请求先落库，本轮回「不允许」，用户批准后 Agent 可重试。这比让 Agent
   一直阻塞等人点头更安全，也不会把会话挂死。
"""

from __future__ import annotations

import uuid
from typing import Any

from worker.runtime.agents.acp_client import (
    AcpClientError,
    AcpSession,
    summarize_updates,
)
from worker.runtime.agents.channel import (
    MAX_RESULT_CHARS,
    REVIEW_STATE,
    TRUST_LEVEL,
    insert_connection,
    load_connection,
    record_call,
    require,
)
from worker.runtime.cleanup import assets_root
from worker.runtime.commands.bus import DispatchError
from worker.runtime.db.rows import now_iso, row_to_dict
from worker.runtime.deps import Deps
from worker.runtime.handlers import approvals
from worker.runtime.models import CommandEnvelope, CommandResult

#: 本地 ACP Agent 的 protocol 值
PROTOCOL = "acp-client"


#: 活跃会话：session_id → AcpSession。会话是有状态的长连接，必须常驻
#: （与 MCP 的短连接相反）—— 每次 prompt 重开进程等于每次从零开始。
_SESSIONS: dict[str, AcpSession] = {}


def _project_root(project_id: str) -> str:
    """会话的 Root/Scope：**只给项目自己的目录**，不是整个 Workspace。

    §13.6「Agent 不直接读取整个 Workspace，必须通过 Root/Scope」的落点。
    目录不存在就先建，否则 Agent 起手就报 cwd 不存在。
    """
    root = assets_root() / project_id
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _make_update_handler(deps: Deps, session_row_id: str) -> Any:
    """流式更新 → ``deps.notify``，让 UI 实时看到 Agent 在干什么。"""

    async def _on_update(params: dict[str, Any]) -> None:
        if deps.notify is None:
            return
        await deps.notify(
            "acp/update",
            {"session_id": session_row_id, "update": params},
        )

    return _on_update


def _make_permission_handler(deps: Deps, env: CommandEnvelope, conn_id: str) -> Any:
    """权限请求 → Approval Center 待批项，本轮一律先拒。

    不阻塞等人点头：桌面端可能没人在看，挂住会话只会让 Agent 卡死。
    落库 + 拒绝，用户批准后 Agent 可重试 —— 失败是安全的默认。
    """

    async def _on_permission(params: dict[str, Any]) -> bool:
        tool = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        title = str((tool or {}).get("title") or params.get("title") or "未命名操作")
        try:
            approvals.create_request(
                deps.repos.conn,
                actor=f"acp:{conn_id}",
                action_type="AcpToolCall",
                target=title,
                risk_summary=f"本地 Agent 请求执行「{title}」，需人工确认",
                payload={"params": params, "connection_id": conn_id},
            )
        except Exception:  # noqa: BLE001 - 落库失败也必须拒绝，不能放行
            return False
        return False

    return _on_permission


def _record_session(
    deps: Deps, *, conn_id: str, project_id: str, external_id: str
) -> str:
    """Session 绑定 Project（§13.6）→ ``agent_sessions``。"""
    session_row_id = f"asess_{uuid.uuid4().hex}"
    deps.repos.conn.execute(
        "INSERT INTO agent_sessions "
        "(id, agent_connection_id, project_id, external_session_id, status, started_at, ended_at) "
        "VALUES (?,?,?,?,?,?,NULL)",
        (session_row_id, conn_id, project_id, external_id, "active", now_iso()),
    )
    deps.repos.conn.commit()
    return session_row_id


def _argv(command: str) -> list[str]:
    from worker.runtime.agents.mcp_client import parse_command

    return parse_command(command)


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    payload = env.payload or {}

    if env.commandType == "AddAcpAgent":
        command = require(payload, "command")
        # 登记即探测：握手不通就不落库（与 AddMcpServer 同一取舍）
        probe = AcpSession(_argv(command))
        try:
            info = await probe.start()
        except AcpClientError as e:
            raise DispatchError(e.code, e.message) from e
        finally:
            await probe.close()

        conn_id = f"acpc_{uuid.uuid4().hex[:12]}"
        insert_connection(
            deps,
            conn_id=conn_id,
            protocol=PROTOCOL,
            endpoint=command,
            local_or_remote="local",
            capabilities=info.get("agentCapabilities") or {},
        )
        deps.repos.conn.commit()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"connection_id": conn_id, "agent_info": info.get("agentInfo") or {}},
        )

    if env.commandType == "StartAcpSession":
        conn_id = require(payload, "connectionId", "connection_id")
        project_id = require(payload, "projectId", "project_id")
        record = load_connection(deps, conn_id, protocol=PROTOCOL, label="本地 ACP Agent")

        root = _project_root(project_id)
        session_row_id = f"asess_{uuid.uuid4().hex}"
        session = AcpSession(
            _argv(str(record["endpoint_or_command"])),
            cwd=root,
            on_update=_make_update_handler(deps, session_row_id),
            on_permission=_make_permission_handler(deps, env, conn_id),
        )
        try:
            await session.start()
            external_id = await session.new_session(root)
        except AcpClientError as e:
            await session.close()
            raise DispatchError(e.code, e.message) from e

        row_id = _record_session(
            deps, conn_id=conn_id, project_id=project_id, external_id=external_id
        )
        _SESSIONS[row_id] = session
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "session_id": row_id,
                "external_session_id": external_id,
                # 明确回报 Root/Scope，便于用户确认 Agent 能看到什么
                "root": root,
                "project_id": project_id,
            },
        )

    if env.commandType == "SendAcpPrompt":
        session_id = require(payload, "sessionId", "session_id")
        text = require(payload, "text")
        live = _SESSIONS.get(session_id)
        if live is None:
            raise DispatchError(
                "NOT_FOUND", f"session {session_id!r} 不存在或已结束，请重新开始会话"
            )
        row = deps.repos.conn.execute(
            "SELECT * FROM agent_sessions WHERE id=?", (session_id,)
        ).fetchone()
        prompt_project_id = str(row["project_id"]) if row and row["project_id"] else None
        conn_id = str(row["agent_connection_id"]) if row else ""

        before = len(live.updates)
        try:
            result = await live.prompt(text)
        except AcpClientError as e:
            record_call(
                deps, env, conn_id=conn_id, task_type="acp:prompt",
                text="", ok=False, project_id=prompt_project_id,
            )
            raise DispatchError(e.code, e.message) from e

        new_updates = live.updates[before:]
        streamed = summarize_updates(new_updates)[:MAX_RESULT_CHARS]
        task_id = record_call(
            deps, env, conn_id=conn_id, task_type="acp:prompt",
            text=streamed, ok=True, project_id=prompt_project_id,
        )
        pending = deps.repos.conn.execute(
            "SELECT COUNT(*) n FROM approval_requests WHERE status='pending' AND actor=?",
            (f"acp:{conn_id}",),
        ).fetchone()["n"]
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "agent_task_id": task_id,
                "stop_reason": result.get("stopReason"),
                "text": streamed,
                "updates": new_updates,
                # 有待批项时 UI 要提示用户去 Approval Center
                "pending_approvals": pending,
                "trust_level": TRUST_LEVEL,
                "review_state": REVIEW_STATE,
            },
        )

    if env.commandType == "EndAcpSession":
        session_id = require(payload, "sessionId", "session_id")
        ending = _SESSIONS.pop(session_id, None)
        if ending is not None:
            await ending.close()
        deps.repos.conn.execute(
            "UPDATE agent_sessions SET status=?, ended_at=? WHERE id=?",
            ("ended", now_iso(), session_id),
        )
        deps.repos.conn.commit()
        return CommandResult(
            ok=True, commandId=env.commandId, detail={"ended": ending is not None}
        )

    if env.commandType == "ListAcpSessions":
        rows = deps.repos.conn.execute(
            "SELECT * FROM agent_sessions ORDER BY started_at DESC"
        ).fetchall()
        sessions = [row_to_dict(r) for r in rows]
        for item in sessions:
            # 库里 active 但进程已没了（worker 重启过）要如实标出
            item["live"] = item["id"] in _SESSIONS
        return CommandResult(
            ok=True, commandId=env.commandId, detail={"sessions": sessions}
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by acp handler",
    )
