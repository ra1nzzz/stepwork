"""STEPWORK MCP server (W7 Phase 3).

A minimal, dependency-free MCP-compatible server that speaks JSON-RPC 2.0
over stdio (line-delimited JSON on ``sys.stdin`` -> ``sys.stdout``).

It exposes a *fixed* set of **read-only** tools that map onto the worker's
Command Bus (``worker.runtime.app.run_command``). The MCP surface is
deliberately a strict subset of the bus:

* Only read-only commands are reachable (``GetConfig`` / ``ListProjects`` /
  ``GetProject`` / ``GetJobStatus`` / ``AnalyzeSource``).
* ``update_config`` / ``UpdateConfig`` is **never** registered. This is the
  root authorization guarantee: secrets can never be written through the MCP
  surface, and ``get_config`` only ever returns the worker-masked view
  (secrets already replaced with ``••••``; only ``hasKey: bool`` is exposed).

The worker process is **not** spawned as a subprocess (sandbox limitation);
``run_command`` is invoked in-process.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from worker.runtime.app import build_envelope, run_command

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "stepwork-mcp"
SERVER_VERSION = "0.1.0"

# Tool name -> Command Bus command_type. Read-only only.
_TOOL_COMMANDS: dict[str, str] = {
    "get_config": "GetConfig",
    "list_projects": "ListProjects",
    "get_project": "GetProject",
    "get_job_status": "GetJobStatus",
    "analyze_source": "AnalyzeSource",
}

# Fixed tool catalogue. NEVER add ``update_config`` / ``UpdateConfig`` here.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_config",
        "description": (
            "Read the merged configuration (secret-masked) plus a resolution "
            "summary. Secrets are already masked by the worker; only "
            "`hasKey: bool` is ever exposed."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_projects",
        "description": "List content projects in the current workspace.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_project",
        "description": "Get a single content project by its id.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
        },
    },
    {
        "name": "get_job_status",
        "description": "Get the status of an asynchronous job by its id.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "analyze_source",
        "description": "Analyze a source by id (read-only metadata / insights).",
        "inputSchema": {
            "type": "object",
            "properties": {"source_id": {"type": "string"}},
            "required": ["source_id"],
        },
    },
]


class McpError(Exception):
    """JSON-RPC level error with a numeric code and message."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def list_tools() -> list[dict[str, Any]]:
    """Return the fixed, read-only tool catalogue (exactly 5 tools)."""
    return TOOLS


def _build_payload(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Translate tool arguments into a Command Bus payload.

    ``update_config`` is intentionally absent: this helper only knows the
    read-only tools. Any unknown tool name yields an empty payload (it will
    be rejected by ``_call_tool`` before reaching the bus).
    """
    if tool_name == "get_project":
        return {"project_id": arguments.get("project_id")}
    if tool_name == "get_job_status":
        return {"job_id": arguments.get("job_id")}
    if tool_name == "analyze_source":
        return {"source_id": arguments.get("source_id")}
    return {}


async def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke a tool: build an envelope and run it through the Command Bus.

    Returns an MCP ``tools/call`` result object. The masked ``detail`` from
    the worker is returned verbatim as the tool result content.
    """
    command_type = _TOOL_COMMANDS.get(name)
    if command_type is None:
        raise McpError(-32602, f"unknown tool: {name}")

    env = build_envelope(
        command_type=command_type,
        source="mcp",
        actor_type="agent",
        payload=_build_payload(name, arguments),
    )

    result = await run_command(env)

    detail = result.get("detail")
    text = json.dumps(detail, ensure_ascii=False)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": not bool(result.get("ok", True)),
    }


def _rpc_result(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def _handle_request(req: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch a single JSON-RPC request. Returns the response object, or
    ``None`` for notifications (which require no response)."""
    method = req.get("method")
    msg_id = req.get("id")

    # Notifications (no id) need no response.
    if msg_id is None:
        return None

    if method == "initialize":
        return _rpc_result(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return _rpc_error(msg_id, -32602, "missing tool name")
        if not isinstance(arguments, dict):
            return _rpc_error(msg_id, -32602, "arguments must be an object")
        try:
            result = asyncio.run(_call_tool(name, arguments))
        except McpError as e:
            return _rpc_error(msg_id, e.code, e.message)
        return _rpc_result(msg_id, result)

    return _rpc_error(msg_id, -32601, f"method not found: {method}")


def _write(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    """stdio JSON-RPC read loop: read line-delimited JSON requests from
    ``sys.stdin`` and write JSON responses to ``sys.stdout``."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            _write(_rpc_error(None, -32700, f"parse error: {e}"))
            continue
        if not isinstance(req, dict):
            _write(_rpc_error(None, -32600, "invalid request: not an object"))
            continue
        resp = _handle_request(req)
        if resp is not None:
            _write(resp)


if __name__ == "__main__":
    main()
