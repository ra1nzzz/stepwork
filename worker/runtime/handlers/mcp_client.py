"""出站 MCP 客户端命令（PRD-AGT-004）。

三条命令，都作用在用户显式登记的外部 MCP Server 上：

- ``AddMcpServer``：登记一个外部 Server。**登记即探测** —— 立刻握手并
  拉 ``tools/list``，连不上就不落库。理由：一条连不上的连接躺在页面上
  比没有更糟，用户得等到真正用它时才发现坏了。
- ``ListMcpTools``：重新拉取工具目录（对方升级后刷新用），顺带把
  ``capabilities`` 与状态回写。
- ``CallMcpTool``：调用一个工具，结果按 PRD-AGT-003 落
  ``agent_tasks`` / ``agent_artifacts``，信任等级 ``external-unverified``。

**外部结果不进正文**：``CallMcpTool`` 只返回文本给 UI 并留痕，不写
``content_versions``。要采用必须由用户在界面上显式操作 —— 这与 §9
「外部来源内容需人工确认」一致。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from worker.runtime.agents.mcp_client import (
    McpClientError,
    McpStdioClient,
    flatten_content,
    parse_command,
)
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.logging_config import mask_secrets
from worker.runtime.models import CommandEnvelope, CommandResult

#: 出站连接的 protocol 值。与入站的 ``mcp`` 分开，否则 Agent Connections
#: 页无法区分「别人调我们」和「我们调别人」，启停语义也会混淆。
PROTOCOL = "mcp-client"

#: 外部拿回的内容一律未经复核（PRD-AGT-003）
TRUST_LEVEL = "external-unverified"
REVIEW_STATE = "pending_review"

#: 单个工具结果入库的文本上限，超出截断（留痕即可，不做归档）
_MAX_RESULT_CHARS = 20000


#: stderr 回显长度上限（够定位问题，又不至于把整篇栈塞进错误消息）
_STDERR_TAIL_CHARS = 500


def _with_diagnostic(e: McpClientError) -> str:
    """错误消息 + 外部 Server 的 stderr 片段。

    stderr 是排查外部 Server 的唯一线索，必须回显；但它常含
    ``api_key=...`` 之类，先过 §11.3 掩码再拼进消息。
    """
    stderr = ""
    if isinstance(e.detail, dict):
        stderr = str(e.detail.get("stderr") or "")
    if not stderr:
        return e.message
    return f"{e.message}（Server 输出：{mask_secrets(stderr)[:_STDERR_TAIL_CHARS]}）"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _require(payload: dict[str, Any], *names: str) -> str:
    """取第一个非空的别名字段（兼容 camelCase / snake_case）。"""
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return str(value)
    raise DispatchError("INVALID_ARGUMENT", f"{names[0]} required")


def _load_connection(deps: Deps, conn_id: str) -> dict[str, Any]:
    row = deps.repos.conn.execute(
        "SELECT * FROM agent_connections WHERE id=?", (conn_id,)
    ).fetchone()
    if row is None:
        raise DispatchError("NOT_FOUND", f"connection {conn_id!r} not found")
    record = _row_to_dict(row)
    if record.get("protocol") != PROTOCOL:
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"connection {conn_id!r} 不是出站 MCP 连接（protocol={record.get('protocol')!r}）",
        )
    # PRD-AGT-007：停用的通道不可调用
    if record.get("status") != "active":
        raise DispatchError(
            "CONNECTION_DISABLED", f"connection {conn_id!r} 已停用，请先在连接页启用"
        )
    return record


async def _probe(command: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """握手 + 拉工具目录；失败抛 :class:`DispatchError`（带原始错误码）。"""
    argv = parse_command(command)
    try:
        async with McpStdioClient(argv) as client:
            info = await client.initialize()
            tools = await client.list_tools()
    except McpClientError as e:
        raise DispatchError(e.code, _with_diagnostic(e)) from e
    return info, tools


def _tool_summaries(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只留 UI 需要的字段，避免把对方的完整 schema 全量存进 capabilities。"""
    return [
        {
            "name": str(t.get("name") or ""),
            "description": str(t.get("description") or ""),
        }
        for t in tools
        if t.get("name")
    ]


def _sync_capabilities(deps: Deps, conn_id: str, tools: list[dict[str, Any]]) -> None:
    """把工具目录写进 ``agent_capabilities``（先清后插，反映对方当前状态）。"""
    db = deps.repos.conn
    db.execute("DELETE FROM agent_capabilities WHERE agent_connection_id=?", (conn_id,))
    now = _now()
    for tool in tools:
        name = str(tool.get("name") or "")
        if not name:
            continue
        db.execute(
            "INSERT INTO agent_capabilities "
            "(id, agent_connection_id, capability_key, capability_schema, enabled, created_at) "
            "VALUES (?,?,?,?,1,?)",
            (
                f"acap_{uuid.uuid4().hex}",
                conn_id,
                name,
                json.dumps(tool.get("inputSchema") or {}, ensure_ascii=False),
                now,
            ),
        )


def _record_call(
    deps: Deps,
    env: CommandEnvelope,
    *,
    conn_id: str,
    tool_name: str,
    text: str,
    ok: bool,
) -> str:
    """按 PRD-AGT-003 留痕：一次调用一条 task，一份结果一条 artifact。

    与 ``agent_record`` 的区别：那里登记的是「外部 Agent 调我们」，这里
    是「我们调外部」。两者共用同一套表与信任等级，故 UI 的复核入口只有
    一个。project_id 允许为空（工具调用未必绑定项目），因此 artifact 只
    在有项目上下文时才落 —— ``agent_artifacts.project_id`` 是 NOT NULL。
    """
    db = deps.repos.conn
    now = _now()
    task_id = f"atask_{uuid.uuid4().hex}"
    actor = env.actor or {}
    project_id = str(env.projectId) if env.projectId else None
    db.execute(
        "INSERT INTO agent_tasks "
        "(id, initiator, target_agent_id, session_id, project_id, task_type, "
        "input_artifact_ids, state, progress, cost, timeout_at, "
        "correlation_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            task_id,
            f"{actor.get('type', 'desktop')}:{actor.get('id', 'ui')}",
            conn_id,
            None,
            project_id,
            f"mcp:{tool_name}",
            "[]",
            "succeeded" if ok else "failed",
            1.0 if ok else 0.0,
            None,
            None,
            env.commandId,
            now,
            now,
        ),
    )
    if ok and project_id:
        db.execute(
            "INSERT INTO agent_artifacts "
            "(id, project_id, agent_task_id, artifact_type, schema_version, "
            "producer_agent_id, content_uri_or_json, source_refs, "
            "trust_level, content_hash, review_state, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"aart_{uuid.uuid4().hex}",
                project_id,
                task_id,
                f"mcp:{tool_name}",
                "1",
                conn_id,
                json.dumps({"text": text, "tool": tool_name}, ensure_ascii=False),
                json.dumps([conn_id]),
                TRUST_LEVEL,
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                REVIEW_STATE,
                now,
            ),
        )
    db.commit()
    return task_id


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    payload = env.payload or {}

    if env.commandType == "AddMcpServer":
        command = _require(payload, "command")
        name = str(payload.get("name") or "").strip() or command.split()[0]
        # 登记即探测：连不上就不落库（见模块 docstring）
        info, tools = await _probe(command)

        conn_id = f"mcpc_{uuid.uuid4().hex[:12]}"
        now = _now()
        server_info = info.get("serverInfo") if isinstance(info, dict) else None
        deps.repos.conn.execute(
            "INSERT INTO agent_connections "
            "(id, protocol, endpoint_or_command, local_or_remote, trust_level, "
            "auth_ref, status, capabilities, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                conn_id,
                PROTOCOL,
                command,
                "local",  # stdio 传输一律本地进程
                TRUST_LEVEL,
                None,
                "active",
                json.dumps(_tool_summaries(tools), ensure_ascii=False),
                now,
                now,
            ),
        )
        _sync_capabilities(deps, conn_id, tools)
        deps.repos.conn.commit()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "connection_id": conn_id,
                "name": name,
                "server_info": server_info or {},
                "tools": _tool_summaries(tools),
            },
        )

    if env.commandType == "ListMcpTools":
        conn_id = _require(payload, "connectionId", "connection_id")
        record = _load_connection(deps, conn_id)
        _info, tools = await _probe(str(record["endpoint_or_command"]))
        deps.repos.conn.execute(
            "UPDATE agent_connections SET capabilities=?, updated_at=? WHERE id=?",
            (json.dumps(_tool_summaries(tools), ensure_ascii=False), _now(), conn_id),
        )
        _sync_capabilities(deps, conn_id, tools)
        deps.repos.conn.commit()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"connection_id": conn_id, "tools": _tool_summaries(tools)},
        )

    if env.commandType == "CallMcpTool":
        conn_id = _require(payload, "connectionId", "connection_id")
        tool_name = _require(payload, "toolName", "tool_name")
        arguments = payload.get("arguments")
        if arguments is not None and not isinstance(arguments, dict):
            raise DispatchError("INVALID_ARGUMENT", "arguments must be an object")

        record = _load_connection(deps, conn_id)
        argv = parse_command(str(record["endpoint_or_command"]))
        try:
            async with McpStdioClient(argv) as client:
                await client.initialize()
                result = await client.call_tool(tool_name, arguments or {})
        except McpClientError as e:
            # 失败也留痕：用户在连接页要看得到「这个 Server 一直在报错」
            _record_call(
                deps, env, conn_id=conn_id, tool_name=tool_name, text="", ok=False
            )
            raise DispatchError(e.code, _with_diagnostic(e)) from e

        text = flatten_content(result)[:_MAX_RESULT_CHARS]
        task_id = _record_call(
            deps, env, conn_id=conn_id, tool_name=tool_name, text=text, ok=True
        )
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "agent_task_id": task_id,
                "tool": tool_name,
                "text": text,
                # 明确告诉 UI：这是外部内容，未经复核，不可直接当正文用
                "trust_level": TRUST_LEVEL,
                "review_state": REVIEW_STATE,
                "is_error": bool(result.get("isError")),
            },
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by mcp_client handler",
    )
