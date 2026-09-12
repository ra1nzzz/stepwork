"""Tests for the STEPWORK MCP server (W7 Phase 3, extended in Tranche 1/2).

Guarantees verified:

1. ``tools/list`` exposes the frozen read-only catalogue and **never**
   ``update_config`` (the root authorization guarantee). Structural drift
   (same name set as ``_TOOL_COMMANDS``, commands ⊆ bus routes, ⊆ the agent
   allowlist, no ``UpdateConfig`` anywhere) is enforced in CI by
   ``scripts/check_mcp_surface.py``; the frozen list here pins the *surface
   itself* so widening it takes a deliberate edit.
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
6. The Tranche 2 read-only tools (``list_content_versions`` /
   ``get_content_version`` / ``list_brand_profiles``) build the camelCase
   payloads the contract defines, omitting optional keys when absent.
7. Every property a tool declares in its ``inputSchema`` really reaches the
   payload — no declared-but-silently-dropped argument.

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

#: Frozen read-only catalogue — the MCP security boundary.
#:
#: Deliberately a second copy of ``server.TOOLS``. Widening the MCP surface is
#: a security decision, so it must require an edit here on purpose rather than
#: ride along silently. The *structural* invariants (``TOOLS`` vs
#: ``_TOOL_COMMANDS`` name sets, commands ⊆ ``_ROUTES``, ⊆
#: ``_AGENT_ALLOWED_COMMANDS``, no ``UpdateConfig``) live in
#: ``scripts/check_mcp_surface.py`` and are not repeated here.
_FROZEN_READ_ONLY_TOOLS: tuple[str, ...] = (
    "get_config",
    "list_projects",
    "get_project",
    "get_job_status",
    "list_jobs",
    "analyze_source",
    "list_content_versions",
    "get_content_version",
    "list_brand_profiles",
)

#: 探针值。用独一无二的对象而非字符串，避免和任何默认值/合法取值撞车。
_PROBE = object()

# A fake *masked* config detail: secrets are already replaced with •••• and
# only ``hasKey`` booleans are present. The MCP layer must return this as-is.
FAKE_MASKED_DETAIL: dict[str, object] = {
    "config": {"llm": {"apiKey": "••••", "model": "gpt-4o"}},
    "resolved": {"ai": {"provider": "openai", "model": "gpt-4o", "hasKey": True}},
}


def test_tools_list_matches_frozen_read_only_catalogue() -> None:
    tools = server.list_tools()
    names = tuple(t["name"] for t in tools)

    assert names == _FROZEN_READ_ONLY_TOOLS, (
        "MCP 工具面是对外安全边界，扩充必须是刻意动作：先确认新工具只读、"
        "已在 _TOOL_COMMANDS 注册，再同步本常量"
    )
    # 结构性自洽（与 check_mcp_surface.py 检查项 C 同源，这里给出更近的失败点）
    assert set(names) == set(server._TOOL_COMMANDS)
    assert "update_config" not in names

    # Defense in depth: the forbidden *command_type* must never be reachable
    # from any registered tool either.
    reachable_command_types = {server._TOOL_COMMANDS[n] for n in names}
    assert "UpdateConfig" not in reachable_command_types


def test_every_declared_property_reaches_the_payload() -> None:
    """每个工具声明的 property 都必须真被 ``_build_payload`` 放进 payload。

    这是 ``scripts/check_mcp_surface.py`` 检查项 G 的**逐工具精确版**：那道门禁
    只能做函数级判断（``_build_payload`` 是一条 if 链，静态切分支会出假报告），
    而这里执行真的函数，能证明「这个工具的这个参数确实被转发（含改名转发）」。

    要挡的反例：给 ``list_jobs`` 的 inputSchema 加上 ``offset`` 却忘了在
    ``_build_payload`` 里读它 —— Agent 传了、命令成功、字段静默丢失、零报错。
    这是「键名写错既不报错也不生效」在 MCP 面的翻版。
    """
    for tool in server.list_tools():
        name = tool["name"]
        for prop in tool["inputSchema"].get("properties", {}):
            payload = server._build_payload(name, {prop: _PROBE})
            assert _PROBE in payload.values(), (
                f"{name} 声明了参数 {prop!r}，但 _build_payload 没把它放进 payload"
                f"（Agent 传了也会被静默丢弃）；实际 payload={payload!r}"
            )


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


def test_content_version_tool_payloads_follow_contract() -> None:
    """Tranche 2 只读工具 payload：契约为 camelCase，可选键缺省不写入。"""
    assert server._build_payload(
        "list_content_versions", {"project_id": "proj-1"}
    ) == {"projectId": "proj-1"}
    assert server._build_payload(
        "list_content_versions",
        {"project_id": "proj-1", "content_type": "script", "limit": 5},
    ) == {"projectId": "proj-1", "contentType": "script", "limit": 5}
    assert server._build_payload(
        "get_content_version", {"version_id": "cv-9"}
    ) == {"versionId": "cv-9"}
    assert server._build_payload("list_brand_profiles", {}) == {}

    assert server._TOOL_COMMANDS["list_content_versions"] == "ListContentVersions"
    assert server._TOOL_COMMANDS["get_content_version"] == "GetContentVersion"
    assert server._TOOL_COMMANDS["list_brand_profiles"] == "ListBrandProfiles"


def test_tools_call_list_content_versions_builds_agent_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_content_versions 走完整 _call_tool 链路：mcp/agent 信封 + camelCase payload。"""
    captured: dict[str, Any] = {}

    async def fake_run_command(
        raw: dict[str, Any], *, db_path: str | None = None
    ) -> dict[str, Any]:
        captured["raw"] = raw
        return {"ok": True, "detail": {"versions": []}}

    monkeypatch.setattr(server, "run_command", fake_run_command)

    result = asyncio.run(
        server._call_tool(
            "list_content_versions",
            {"project_id": "proj-1", "content_type": "transcript"},
        )
    )

    env = captured["raw"]
    assert env["source"] == "mcp"
    assert env["actor"]["type"] == "agent"
    assert env["commandType"] == "ListContentVersions"
    assert env["payload"] == {"projectId": "proj-1", "contentType": "transcript"}
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == {"versions": []}


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
