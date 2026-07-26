"""出站 MCP 客户端测试（PRD-AGT-004）。

用**真子进程**跑一个最小 MCP Server（临时写出的 Python 脚本），端到端
验证 spawn → initialize → tools/list → tools/call 全链路，而不是 mock
掉传输层 —— 这条链路的坑（管道、超时、非 JSON 噪声行、Server 崩溃）恰
恰全在传输层，mock 掉就什么也没测。

同时覆盖故障面：Server 崩溃、超时、stdout 混日志、响应过大、坏命令，
以及 PRD-AGT-003 的留痕（agent_tasks / agent_artifacts + 信任等级）。
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.agents.mcp_client import (
    McpClientError,
    McpStdioClient,
    flatten_content,
    parse_command,
)
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"

# --------------------------------------------------------------------------
# 测试用的最小 MCP Server（行分隔 JSON-RPC，与 mcp/server.py 同框架）
# --------------------------------------------------------------------------

_GOOD_SERVER = '''
import json, sys
def w(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    m, i = req.get("method"), req.get("id")
    if m == "initialize":
        w({"jsonrpc":"2.0","id":i,"result":{"protocolVersion":"2024-11-05",
           "capabilities":{"tools":{}},
           "serverInfo":{"name":"fake-kb","version":"9.9.9"}}})
    elif m == "tools/list":
        w({"jsonrpc":"2.0","id":i,"result":{"tools":[
            {"name":"search","description":"搜知识库",
             "inputSchema":{"type":"object","properties":{"q":{"type":"string"}}}},
            {"name":"echo","description":"回声"}]}})
    elif m == "tools/call":
        p = req.get("params") or {}
        name = p.get("name"); args = p.get("arguments") or {}
        if name == "search":
            w({"jsonrpc":"2.0","id":i,"result":{"content":[
                {"type":"text","text":"结果：" + str(args.get("q",""))},
                {"type":"image","data":"BASE64BLOB"}]}})
        elif name == "boom":
            w({"jsonrpc":"2.0","id":i,"error":{"code":-32000,"message":"tool exploded"}})
        else:
            w({"jsonrpc":"2.0","id":i,"result":{"content":[{"type":"text","text":"ok"}]}})
    else:
        w({"jsonrpc":"2.0","id":i,"error":{"code":-32601,"message":"nope"}})
'''

# stdout 混打日志的 Server：客户端必须跳过非 JSON 行而不是报错
_NOISY_SERVER = '''
import json, sys
def w(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    sys.stdout.write("INFO starting up, not json at all\\n"); sys.stdout.flush()
    w({"jsonrpc":"2.0","id":req.get("id"),"result":{"protocolVersion":"2024-11-05",
       "serverInfo":{"name":"noisy","version":"1"}}})
'''

# 立刻崩溃的 Server
_CRASH_SERVER = '''
import sys
sys.stderr.write("fatal: missing API key\\n")
sys.exit(3)
'''

# 收到请求后装死（永不回包）—— 触发超时
_HANG_SERVER = '''
import sys, time
for line in sys.stdin:
    time.sleep(30)
'''


def _write_server(tmp_path: Path, source: str, name: str) -> str:
    """把脚本写到磁盘并返回可执行的命令行。"""
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    # 路径可能含空格，命令行里要加引号（parse_command 负责剥离）
    return f'"{sys.executable}" "{path}"'


def _env(
    command_type: str,
    payload: dict[str, Any] | None = None,
    *,
    project_id: str | None = None,
) -> dict[str, Any]:
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "desktop", "id": "ui"},
        "source": "ui",
        "workspaceId": "ws-local",
        "projectId": project_id,
        "requestedAt": datetime.now(UTC).isoformat(),
        "payload": payload or {},
    }


def _new_db(tmp_path: Path) -> tuple[sqlite3.Connection, Repos]:
    conn = connect(str(tmp_path / "mcpc.db"))
    run_migrations(conn, _MIG_DIR)
    return conn, Repos(conn)


def _run(raw: dict[str, Any], deps: Deps) -> dict[str, Any]:
    return asyncio.run(dispatch(raw, deps))


# --------------------------------------------------------------------------
# 传输层
# --------------------------------------------------------------------------


def test_parse_command_keeps_windows_backslashes() -> None:
    """Windows 路径里的反斜杠不能被 shlex 当转义符吃掉。"""
    argv = parse_command(r'"C:\Tools\srv.exe" --flag a\b')
    assert argv[0] == r"C:\Tools\srv.exe"
    assert argv[1] == "--flag"
    assert argv[2] == r"a\b"


def test_parse_command_rejects_empty() -> None:
    with pytest.raises(McpClientError) as e:
        parse_command("   ")
    assert e.value.code == "MCP_CLIENT_BAD_COMMAND"


async def test_handshake_and_tools_over_real_subprocess(tmp_path: Path) -> None:
    cmd = _write_server(tmp_path, _GOOD_SERVER, "good.py")
    async with McpStdioClient(parse_command(cmd)) as client:
        info = await client.initialize()
        assert info["serverInfo"]["name"] == "fake-kb"
        tools = await client.list_tools()
        assert [t["name"] for t in tools] == ["search", "echo"]

        result = await client.call_tool("search", {"q": "量子计算"})
        text = flatten_content(result)
        assert "结果：量子计算" in text
        # 非文本块只留类型占位，不把 base64 整块塞进文本
        assert "BASE64BLOB" not in text
        assert "[image]" in text


async def test_skips_non_json_stdout_lines(tmp_path: Path) -> None:
    """Server 往 stdout 混打日志是常态，不能因此判定协议错误。"""
    cmd = _write_server(tmp_path, _NOISY_SERVER, "noisy.py")
    async with McpStdioClient(parse_command(cmd)) as client:
        info = await client.initialize()
        assert info["serverInfo"]["name"] == "noisy"


async def test_server_crash_reports_stderr(tmp_path: Path) -> None:
    """Server 崩了要把 stderr 带回来 —— 否则用户只看到「失败」无从排查。"""
    cmd = _write_server(tmp_path, _CRASH_SERVER, "crash.py")
    async with McpStdioClient(parse_command(cmd)) as client:
        with pytest.raises(McpClientError) as e:
            await client.initialize()
    assert e.value.code in {"MCP_CLIENT_EOF", "MCP_CLIENT_DISCONNECTED"}
    assert "missing API key" in str(e.value.detail or {})


async def test_timeout_does_not_hang_worker(tmp_path: Path) -> None:
    """装死的 Server 必须在超时后放行，而不是把 worker 拖住。"""
    cmd = _write_server(tmp_path, _HANG_SERVER, "hang.py")
    async with McpStdioClient(parse_command(cmd), timeout=0.6) as client:
        with pytest.raises(McpClientError) as e:
            await client.initialize()
    assert e.value.code == "MCP_CLIENT_TIMEOUT"


async def test_spawn_failure_is_typed(tmp_path: Path) -> None:
    with pytest.raises(McpClientError) as e:
        async with McpStdioClient(["definitely-not-a-real-binary-xyz"]) as client:
            await client.initialize()
    assert e.value.code == "MCP_CLIENT_SPAWN_FAILED"


async def test_rpc_error_surfaces_message(tmp_path: Path) -> None:
    cmd = _write_server(tmp_path, _GOOD_SERVER, "good.py")
    async with McpStdioClient(parse_command(cmd)) as client:
        await client.initialize()
        with pytest.raises(McpClientError) as e:
            await client.call_tool("boom", {})
    assert e.value.code == "MCP_CLIENT_RPC_ERROR"
    assert "tool exploded" in e.value.message


# --------------------------------------------------------------------------
# 命令层
# --------------------------------------------------------------------------


def test_add_mcp_server_probes_and_persists(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        cmd = _write_server(tmp_path, _GOOD_SERVER, "good.py")
        res = _run(_env("AddMcpServer", {"command": cmd, "name": "KB"}), Deps(repos=repos))
        assert res["ok"] is True, res
        assert res["detail"]["server_info"]["name"] == "fake-kb"
        assert [t["name"] for t in res["detail"]["tools"]] == ["search", "echo"]

        conn_id = res["detail"]["connection_id"]
        row = conn.execute(
            "SELECT * FROM agent_connections WHERE id=?", (conn_id,)
        ).fetchone()
        # 出站连接用独立 protocol，否则与入站 mcp 混淆、启停语义不清
        assert row["protocol"] == "mcp-client"
        assert row["trust_level"] == "external-unverified"
        caps = conn.execute(
            "SELECT capability_key FROM agent_capabilities WHERE agent_connection_id=?",
            (conn_id,),
        ).fetchall()
        assert sorted(c["capability_key"] for c in caps) == ["echo", "search"]
    finally:
        conn.close()


def test_add_mcp_server_does_not_persist_unreachable(tmp_path: Path) -> None:
    """连不上的 Server 不该躺在连接页上装作可用。"""
    conn, repos = _new_db(tmp_path)
    try:
        cmd = _write_server(tmp_path, _CRASH_SERVER, "crash.py")
        res = _run(_env("AddMcpServer", {"command": cmd}), Deps(repos=repos))
        assert res["ok"] is False, res
        assert conn.execute("SELECT COUNT(*) n FROM agent_connections").fetchone()["n"] == 0
    finally:
        conn.close()


def test_call_tool_records_task_and_artifact(tmp_path: Path) -> None:
    """PRD-AGT-003：外部结果必须带来源与信任等级。"""
    conn, repos = _new_db(tmp_path)
    try:
        cmd = _write_server(tmp_path, _GOOD_SERVER, "good.py")
        deps = Deps(repos=repos)
        conn_id = _run(_env("AddMcpServer", {"command": cmd}), deps)["detail"][
            "connection_id"
        ]
        pid = _run(_env("CreateProject", {"title": "P"}), deps)["detail"]["project"]["id"]

        res = _run(
            _env(
                "CallMcpTool",
                {"connectionId": conn_id, "toolName": "search", "arguments": {"q": "X"}},
                project_id=pid,
            ),
            deps,
        )
        assert res["ok"] is True, res
        assert "结果：X" in res["detail"]["text"]
        assert res["detail"]["trust_level"] == "external-unverified"
        assert res["detail"]["review_state"] == "pending_review"

        task = conn.execute(
            "SELECT * FROM agent_tasks WHERE id=?", (res["detail"]["agent_task_id"],)
        ).fetchone()
        assert task["task_type"] == "mcp:search"
        assert task["target_agent_id"] == conn_id
        art = conn.execute(
            "SELECT * FROM agent_artifacts WHERE agent_task_id=?", (task["id"],)
        ).fetchone()
        assert art["trust_level"] == "external-unverified"
        assert art["review_state"] == "pending_review"

        # 外部内容绝不自动进正文
        assert conn.execute("SELECT COUNT(*) n FROM content_versions").fetchone()["n"] == 0
    finally:
        conn.close()


def test_call_tool_failure_is_recorded_too(tmp_path: Path) -> None:
    """失败也要留痕，否则连接页看不出「这个 Server 一直在报错」。"""
    conn, repos = _new_db(tmp_path)
    try:
        cmd = _write_server(tmp_path, _GOOD_SERVER, "good.py")
        deps = Deps(repos=repos)
        conn_id = _run(_env("AddMcpServer", {"command": cmd}), deps)["detail"][
            "connection_id"
        ]
        res = _run(
            _env("CallMcpTool", {"connectionId": conn_id, "toolName": "boom"}), deps
        )
        assert res["ok"] is False
        task = conn.execute("SELECT * FROM agent_tasks").fetchone()
        assert task["state"] == "failed"
        assert task["task_type"] == "mcp:boom"
    finally:
        conn.close()


def test_disabled_connection_is_rejected(tmp_path: Path) -> None:
    """PRD-AGT-007：停用的通道不能再被调用。"""
    conn, repos = _new_db(tmp_path)
    try:
        cmd = _write_server(tmp_path, _GOOD_SERVER, "good.py")
        deps = Deps(repos=repos)
        conn_id = _run(_env("AddMcpServer", {"command": cmd}), deps)["detail"][
            "connection_id"
        ]
        _run(
            _env(
                "SetAgentConnectionStatus",
                {"connectionId": conn_id, "status": "inactive"},
            ),
            deps,
        )
        res = _run(
            _env("CallMcpTool", {"connectionId": conn_id, "toolName": "echo"}), deps
        )
        assert res["ok"] is False
        assert str(res["error"]).startswith("CONNECTION_DISABLED")
    finally:
        conn.close()


def test_inbound_connection_cannot_be_called_as_client(tmp_path: Path) -> None:
    """入站 ``mcp`` 连接行不是可拨出的 Server，误调必须被挡。"""
    conn, repos = _new_db(tmp_path)
    try:
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO agent_connections (id, protocol, endpoint_or_command, "
            "local_or_remote, trust_level, auth_ref, status, capabilities, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("conn_mcp", "mcp", "stdio:mcp", "local", "external-unverified",
             None, "active", "[]", now, now),
        )
        conn.commit()
        res = _run(
            _env("CallMcpTool", {"connectionId": "conn_mcp", "toolName": "echo"}),
            Deps(repos=repos),
        )
        assert res["ok"] is False
        assert str(res["error"]).startswith("INVALID_ARGUMENT")
    finally:
        conn.close()


def test_list_mcp_tools_refreshes_catalogue(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        cmd = _write_server(tmp_path, _GOOD_SERVER, "good.py")
        deps = Deps(repos=repos)
        conn_id = _run(_env("AddMcpServer", {"command": cmd}), deps)["detail"][
            "connection_id"
        ]
        res = _run(_env("ListMcpTools", {"connectionId": conn_id}), deps)
        assert res["ok"] is True, res
        assert [t["name"] for t in res["detail"]["tools"]] == ["search", "echo"]
        caps = conn.execute(
            "SELECT COUNT(*) n FROM agent_capabilities WHERE agent_connection_id=?",
            (conn_id,),
        ).fetchone()["n"]
        # 先清后插：刷新不能把能力行翻倍
        assert caps == 2
    finally:
        conn.close()


def test_external_agents_cannot_reach_mcp_client_commands(tmp_path: Path) -> None:
    """默认拒绝清单：外部 Agent 不得借 STEPWORK 去调别的 Server（跳板）。"""
    conn, repos = _new_db(tmp_path)
    try:
        raw = _env("AddMcpServer", {"command": "whatever"})
        raw["actor"] = {"type": "agent", "id": "evil"}
        raw["source"] = "mcp"
        res = _run(raw, Deps(repos=repos))
        assert res["ok"] is False, res
        # 必须是被允许清单挡下的，而不是碰巧因别的原因失败
        assert str(res["error"]).startswith("FORBIDDEN_ACTOR"), res
        # 不该真去 spawn 进程，也不该留下连接
        assert conn.execute("SELECT COUNT(*) n FROM agent_connections").fetchone()["n"] == 0
    finally:
        conn.close()


# 返回一条**大但合法**的响应（远超 asyncio 默认 64KB 单行上限）。
# 搜索/读文件类 MCP 工具的正常结果就这么大，必须能收下。
_BIG_SERVER = '''
import json, sys
for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":req.get("id"),
        "result":{"serverInfo":{"name":"big","version":"1"},"blob":"A"*300000}}) + "\\n")
    sys.stdout.flush()
'''

# 单行响应超过硬上限：必须给明确错误码，而不是裸 ValueError 或静默挂死
_FLOOD_SERVER = '''
import json, sys
for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":req.get("id"),
        "result":{"blob":"A"*5000000}}) + "\\n")
    sys.stdout.flush()
'''


async def test_large_but_legal_response_is_accepted(tmp_path: Path) -> None:
    """300KB 的正常结果不该被 asyncio 默认的 64KB 单行上限打爆。"""
    cmd = _write_server(tmp_path, _BIG_SERVER, "big.py")
    async with McpStdioClient(parse_command(cmd), timeout=15.0) as client:
        info = await client.initialize()
    assert info["serverInfo"]["name"] == "big"
    assert len(info["blob"]) == 300000


async def test_oversized_response_is_capped(tmp_path: Path) -> None:
    """失控/恶意 Server 不能靠超大输出把 worker 撑爆。"""
    cmd = _write_server(tmp_path, _FLOOD_SERVER, "flood.py")
    async with McpStdioClient(parse_command(cmd), timeout=15.0) as client:
        with pytest.raises(McpClientError) as e:
            await client.initialize()
    assert e.value.code == "MCP_CLIENT_RESPONSE_TOO_LARGE"
