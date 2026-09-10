"""与 ``stepwork-hotspot-mcp`` 的对接（进程外，P2）。

这里**不内联任何抓取逻辑**：热点源的形态变化比产品快，塞进主仓会让「源挂了」
变成「产品发版」。本模块只做三件事：找连接、发 JSON-RPC、解析回包形状。

连接从哪来：``agent_connections`` 里 protocol=``mcp-client`` 的记录。
优先用 payload 显式给的 ``connectionId``；没给就按 endpoint 里含
``stepwork-hotspot-mcp`` 自动找一条 —— 用户只想在 GUI 里点「发现热点」，
不该先去查一遍连接 id。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from worker.runtime.agents.mcp_client import (
    McpClientError,
    McpStdioClient,
    describe_error,
    flatten_content,
    parse_command,
)
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps

logger = logging.getLogger("worker.runtime")

#: 自动查找连接时用来识别热点 Server 的关键字（命令或 URL 里含它即可）
SERVER_MARKER = "stepwork-hotspot-mcp"

#: 匹配用的规范化形式。
#:
#: 为什么不能直接 ``in`` 判断：启动命令的合法写法至少有三种 ——
#: ``stepwork-hotspot-mcp``（console script）、
#: ``python -m stepwork_hotspot_mcp.server``（模块）、
#: ``.../stepwork-hotspot-mcp/src/.../server.py``（源码路径）。
#: 连字符与下划线混着来，直接子串匹配会漏掉后两种。
_MARKER_KEY = "stepworkhotspotmcp"


def _marker_key(text: str) -> str:
    return text.replace("-", "").replace("_", "").lower()

#: 出站 MCP 连接的 protocol（与 handlers/mcp_client.py 一致）
PROTOCOL = "mcp-client"

#: tools/call 的超时（秒）。
#:
#: 默认 20s **不够**：上游 11 个免登录源是**串行**抓的，实测 20–25s，
#: 真机验收第一次就撞在「MCP Server 在 20s 内无响应」上。抓取本身不慢，
#: 慢的是「一次调一次全量抓」这个形状，所以把超时放宽而不是拆调用
#: （拆了会让 GUI 的进度反馈变复杂，且用户只关心最终列表）。
#: 可用 ``STEPWORK_HOTSPOT_TIMEOUT`` 覆盖。
DEFAULT_CALL_TIMEOUT = float(os.environ.get("STEPWORK_HOTSPOT_TIMEOUT", "90"))


def resolve_connection(deps: Deps, connection_id: str | None = None) -> dict[str, Any]:
    """定位热点 MCP 连接。

    Args:
        connection_id: 显式指定；为空则自动找。
    """
    conn = deps.repos.conn
    if connection_id:
        row = conn.execute(
            "SELECT * FROM agent_connections WHERE id=?", (connection_id,)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"connection {connection_id!r} not found")
        record = {k: row[k] for k in row.keys()}
        if record.get("protocol") != PROTOCOL:
            raise DispatchError(
                "INVALID_ARGUMENT",
                f"connection {connection_id!r} 不是 MCP 连接"
                f"（protocol={record.get('protocol')!r}）",
            )
        if record.get("status") != "active":
            raise DispatchError(
                "CONNECTION_DISABLED", f"connection {connection_id!r} 已停用"
            )
        return record

    rows = conn.execute(
        "SELECT * FROM agent_connections WHERE protocol=? AND status='active' "
        "ORDER BY updated_at DESC",
        (PROTOCOL,),
    ).fetchall()
    records = [{k: row[k] for k in row.keys()} for row in rows]
    matched = [
        r
        for r in records
        if _MARKER_KEY in _marker_key(str(r.get("endpoint_or_command") or ""))
    ]
    if not matched:
        hint = (
            "未找到热点 MCP 连接。请先登记：AddMcpServer "
            "{command: 'stepwork-hotspot-mcp'}（或含 "
            f"{SERVER_MARKER} 的启动命令）"
        )
        if records:
            names = ", ".join(str(r.get("id")) for r in records[:3])
            hint += f"；当前已有 MCP 连接（{names}）都不是热点 Server"
        raise DispatchError("NOT_FOUND", hint)
    if len(matched) > 1:
        # 不猜：多条就让用户点名，猜错了更难查
        ids = ", ".join(str(r.get("id")) for r in matched)
        raise DispatchError(
            "INVALID_ARGUMENT", f"匹配到多条热点 MCP 连接（{ids}），请显式传 connectionId"
        )
    return matched[0]


def _payload_text(result: Any) -> dict[str, Any]:
    """把 MCP 回包（content[].text 是 JSON 串）解成 dict。"""
    text = flatten_content(result)
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError) as e:
        raise DispatchError("UPSTREAM_ERROR", f"热点 Server 返回的不是 JSON: {e}") from None
    if not isinstance(parsed, dict):
        raise DispatchError("UPSTREAM_ERROR", "热点 Server 返回的 JSON 不是对象")
    return parsed


async def call_hotspot_tool(
    deps: Deps,
    tool: str,
    arguments: dict[str, Any],
    *,
    connection_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    """调用热点 Server 的一个工具。返回 ``(解析后的 dict, connection_id)``。"""
    record = resolve_connection(deps, connection_id)
    conn_id = str(record["id"])
    argv = parse_command(str(record["endpoint_or_command"]))
    try:
        async with McpStdioClient(argv, timeout=DEFAULT_CALL_TIMEOUT) as client:
            await client.initialize()
            result = await client.call_tool(tool, arguments)
    except RuntimeError as e:
        # McpClientError 是 RuntimeError 的子类；这里统一转译，且不吞细节。
        # 用 describe_error 而不是 str(e)：后者会丢掉 detail.stderr，而 Server
        # 起不来时唯一有用的线索就在那里（真机验收撞过 —— 报「在响应前退出」，
        # 实际原因是 ModuleNotFoundError，包没装）。
        hint = describe_error(e) if isinstance(e, McpClientError) else str(e)
        raise DispatchError("UPSTREAM_ERROR", f"热点 Server 调用失败：{hint}") from e
    return _payload_text(result), conn_id


async def discover(
    deps: Deps,
    *,
    sources: list[str] | None = None,
    limit: int = 30,
    window_hours: int = 48,
    query: str | None = None,
    connection_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    """调 ``discover_hotspots``。"""
    arguments: dict[str, Any] = {"limit": limit, "windowHours": window_hours}
    if sources:
        arguments["sources"] = sources
    if query:
        arguments["query"] = query
    return await call_hotspot_tool(
        deps, "discover_hotspots", arguments, connection_id=connection_id
    )


async def list_sources(deps: Deps, *, connection_id: str | None = None) -> tuple[Any, str]:
    """调 ``list_sources``。"""
    return await call_hotspot_tool(deps, "list_sources", {}, connection_id=connection_id)
