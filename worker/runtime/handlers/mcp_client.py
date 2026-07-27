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

import json
import uuid
from typing import Any

from worker.runtime.agents.channel import (
    MAX_RESULT_CHARS,
    REVIEW_STATE,
    TRUST_LEVEL,
    insert_connection,
    load_connection,
    record_call,
    require,
    sync_capabilities,
)
from worker.runtime.agents.mcp_client import (
    McpClientError,
    McpStdioClient,
    flatten_content,
    parse_command,
)
from worker.runtime.commands.bus import DispatchError
from worker.runtime.db.rows import now_iso
from worker.runtime.deps import Deps
from worker.runtime.logging_config import mask_secrets
from worker.runtime.models import CommandEnvelope, CommandResult

#: 出站连接的 protocol 值。与入站的 ``mcp`` 分开，否则 Agent Connections
#: 页无法区分「别人调我们」和「我们调别人」，启停语义也会混淆。
PROTOCOL = "mcp-client"



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


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    payload = env.payload or {}

    if env.commandType == "AddMcpServer":
        command = require(payload, "command")
        name = str(payload.get("name") or "").strip() or command.split()[0]
        # 登记即探测：连不上就不落库（见模块 docstring）
        info, tools = await _probe(command)

        conn_id = f"mcpc_{uuid.uuid4().hex[:12]}"
        server_info = info.get("serverInfo") if isinstance(info, dict) else None
        insert_connection(
            deps,
            conn_id=conn_id,
            protocol=PROTOCOL,
            endpoint=command,
            local_or_remote="local",  # stdio 传输一律本地进程
            capabilities=_tool_summaries(tools),
        )
        sync_capabilities(deps, conn_id, tools)
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
        conn_id = require(payload, "connectionId", "connection_id")
        record = load_connection(deps, conn_id, protocol=PROTOCOL, label="出站 MCP 连接")
        _info, tools = await _probe(str(record["endpoint_or_command"]))
        deps.repos.conn.execute(
            "UPDATE agent_connections SET capabilities=?, updated_at=? WHERE id=?",
            (json.dumps(_tool_summaries(tools), ensure_ascii=False), now_iso(), conn_id),
        )
        sync_capabilities(deps, conn_id, tools)
        deps.repos.conn.commit()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"connection_id": conn_id, "tools": _tool_summaries(tools)},
        )

    if env.commandType == "CallMcpTool":
        conn_id = require(payload, "connectionId", "connection_id")
        tool_name = require(payload, "toolName", "tool_name")
        arguments = payload.get("arguments")
        if arguments is not None and not isinstance(arguments, dict):
            raise DispatchError("INVALID_ARGUMENT", "arguments must be an object")

        record = load_connection(deps, conn_id, protocol=PROTOCOL, label="出站 MCP 连接")
        argv = parse_command(str(record["endpoint_or_command"]))
        try:
            async with McpStdioClient(argv) as client:
                await client.initialize()
                result = await client.call_tool(tool_name, arguments or {})
        except McpClientError as e:
            # 失败也留痕：用户在连接页要看得到「这个 Server 一直在报错」
            record_call(
                deps, env, conn_id=conn_id, task_type=f"mcp:{tool_name}", text="", ok=False
            )
            raise DispatchError(e.code, _with_diagnostic(e)) from e

        text = flatten_content(result)[:MAX_RESULT_CHARS]
        task_id = record_call(
            deps, env, conn_id=conn_id, task_type=f"mcp:{tool_name}", text=text, ok=True
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
