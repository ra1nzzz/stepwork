"""Tests for the STEPWORK MCP server (W7 Phase 3, extended in Tranche 1).

Guarantees verified:

1. ``tools/list`` exposes exactly the 6 read-only tools and **never**
   ``update_config`` (the root authorization guarantee).
2. ``tools/call`` for ``get_config`` builds a Command Bus envelope with
   ``source == "mcp"`` and ``actor.type == "agent"`` and returns the
   worker-masked ``detail`` unchanged.
3. ``analyze_source`` maps its arguments onto the payload the worker
   handler actually accepts (``transcript_version_id`` / ``text`` /
   ``brand`` — never the legacy ``source_id``).
4. Failed commands surface the ``CommandResult`` error message in the tool
   result content (agents must be able to act on failures).
5. ``list_jobs`` builds the ListJobs payload per the Tranche 1 contract
   (``states`` / ``limit`` both optional, omitted when absent).

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


def test_tools_list_has_exactly_six_read_only_tools() -> None:
    tools = server.list_tools()
    names = [t["name"] for t in tools]

    # Deliberate count bump (Tranche 1): the read-only ``list_jobs`` tool
    # (ListJobs) joined the catalogue. ``update_config`` stays unreachable.
    assert len(tools) == 6
    assert names == [
        "get_config",
        "list_projects",
        "get_project",
        "get_job_status",
        "list_jobs",
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


def test_analyze_source_maps_handler_accepted_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """analyze_source 参数必须映射为 worker handler 接受的 payload 键。"""
    captured: dict[str, Any] = {}

    async def fake_run_command(
        raw: dict[str, Any], *, db_path: str | None = None
    ) -> dict[str, Any]:
        captured["raw"] = raw
        return {"ok": True, "detail": {"sentiment": "positive"}}

    monkeypatch.setattr(server, "run_command", fake_run_command)

    asyncio.run(
        server._call_tool(
            "analyze_source",
            {"transcript_version_id": "cv-1", "brand": "brand-1"},
        )
    )
    payload = captured["raw"]["payload"]
    assert payload == {"transcript_version_id": "cv-1", "brand": "brand-1"}
    assert "source_id" not in payload

    asyncio.run(server._call_tool("analyze_source", {"text": "raw text"}))
    assert captured["raw"]["payload"] == {"text": "raw text"}


def test_list_jobs_payload_follows_contract() -> None:
    """ListJobs payload：states / limit 可选，缺省不写入。"""
    assert server._build_payload("list_jobs", {}) == {}
    assert server._build_payload(
        "list_jobs", {"states": ["running", "failed"], "limit": 10}
    ) == {"states": ["running", "failed"], "limit": 10}
    assert server._TOOL_COMMANDS["list_jobs"] == "ListJobs"


def test_tools_call_error_includes_command_result_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失败结果必须携带 CommandResult 的 error 信息，而非空对象。"""

    async def fake_run_command(
        raw: dict[str, Any], *, db_path: str | None = None
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "NOT_FOUND: job 'j-404' not found",
            "detail": {},
        }

    monkeypatch.setattr(server, "run_command", fake_run_command)

    result = asyncio.run(server._call_tool("get_job_status", {"job_id": "j-404"}))

    assert result["isError"] is True
    body = json.loads(result["content"][0]["text"])
    assert body["error"] == "NOT_FOUND: job 'j-404' not found"
    assert "detail" in body
