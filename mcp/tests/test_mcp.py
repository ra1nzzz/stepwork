"""Tests for the STEPWORK MCP server (W7 Phase 3).

Two guarantees are verified:

1. ``tools/list`` exposes exactly the 5 read-only tools and **never**
   ``update_config`` (the root authorization guarantee).
2. ``tools/call`` for ``get_config`` builds a Command Bus envelope with
   ``source == "mcp"`` and ``actor.type == "agent"`` and returns the
   worker-masked ``detail`` unchanged.

``run_command`` is monkeypatched so the tests exercise the MCP layer in
isolation (no real worker / DB needed). The real ``build_envelope`` is used
so the produced envelope shape is asserted end-to-end.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

import mcp.server as server

# A fake *masked* config detail: secrets are already replaced with •••• and
# only ``hasKey`` booleans are present. The MCP layer must return this as-is.
FAKE_MASKED_DETAIL: dict[str, object] = {
    "config": {"llm": {"apiKey": "••••", "model": "gpt-4o"}},
    "resolved": {"ai": {"provider": "openai", "model": "gpt-4o", "hasKey": True}},
}


def test_tools_list_has_exactly_five_read_only_tools() -> None:
    tools = server.list_tools()
    names = [t["name"] for t in tools]

    assert len(tools) == 5
    assert names == [
        "get_config",
        "list_projects",
        "get_project",
        "get_job_status",
        "analyze_source",
    ]
    assert "update_config" not in names

    # Defense in depth: the forbidden *command_type* must never be reachable
    # from any registered tool either.
    reachable_command_types = {server._TOOL_COMMANDS[n] for n in names}
    assert "UpdateConfig" not in reachable_command_types


def test_tools_call_get_config_builds_agent_envelope_and_returns_masked_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_command(
        raw: dict[str, Any], *, db_path: str | None = None
    ) -> dict[str, Any]:
        captured["raw"] = raw
        return {
            "ok": True,
            "commandId": raw.get("commandId"),
            "detail": FAKE_MASKED_DETAIL,
        }

    # Replace the worker call; the real build_envelope is kept so the
    # produced envelope is asserted as the MCP layer actually builds it.
    monkeypatch.setattr(server, "run_command", fake_run_command)

    result = asyncio.run(server._call_tool("get_config", {}))

    env = captured["raw"]
    assert env["source"] == "mcp"
    assert env["actor"]["type"] == "agent"
    assert env["commandType"] == "GetConfig"

    # The masked detail must be returned verbatim as the tool result.
    content_text = result["content"][0]["text"]
    assert json.loads(content_text) == FAKE_MASKED_DETAIL
    assert result["isError"] is False
