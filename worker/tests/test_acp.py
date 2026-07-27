"""ACP 客户端测试（PRD-AGT-006）。

ACP 与 MCP 的本质区别是**双向**：Agent 会主动往回发流式进度和权限请求。
所以测试用的假 Agent 不是「收一条回一条」，而是真的会在 prompt 期间推
若干 ``session/update`` 通知，并发起 ``session/request_permission`` 反向
请求 —— 只有这样才测得到消息泵的分流逻辑。

覆盖 SYSTEM_SPEC §13.6 的三条硬约束：Session 绑定 Project、Root/Scope
不给整个 Workspace、权限请求必须过人（落 Approval Center 且默认拒绝）。
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.agents.acp_client import (
    AcpClientError,
    AcpSession,
    build_permission_outcome,
    summarize_updates,
)
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.handlers import acp

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"

# --------------------------------------------------------------------------
# 假 ACP Agent：会流式推送，也会反向请求权限
# --------------------------------------------------------------------------

_AGENT = '''
import json, sys, os

def w(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    m, i = req.get("method"), req.get("id")
    if m == "initialize":
        w({"jsonrpc":"2.0","id":i,"result":{"protocolVersion":1,
           "agentInfo":{"name":"fake-acp","version":"1.2.3"},
           "agentCapabilities":{"promptCapabilities":{"image":False}}}})
    elif m == "session/new":
        p = req.get("params") or {}
        # 把拿到的 cwd 回显，测试据此验证 Root/Scope
        w({"jsonrpc":"2.0","id":i,"result":{"sessionId":"sess-1","_cwd":p.get("cwd")}})
    elif m == "session/prompt":
        sid = (req.get("params") or {}).get("sessionId")
        # 1) 先推两条流式更新（无 id 的通知）
        w({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":sid,
           "update":{"sessionUpdate":"agent_message_chunk",
                     "content":{"type":"text","text":"正在分析"}}}})
        w({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":sid,
           "update":{"sessionUpdate":"agent_message_chunk",
                     "content":{"type":"text","text":"素材……"}}}})
        # 2) 反向请求权限（有 id），等我们回包
        w({"jsonrpc":"2.0","id":9001,"method":"session/request_permission",
           "params":{"sessionId":sid,"toolCall":{"toolCallId":"call_001","title":"删除项目文件","kind":"delete"},
             "options":[{"optionId":"allow-once","name":"允许一次","kind":"allow_once"},
                        {"optionId":"allow-always","name":"总是允许","kind":"allow_always"},
                        {"optionId":"reject-once","name":"拒绝","kind":"reject_once"}]}})
        decision = None
        for reply in sys.stdin:
            reply = reply.strip()
            if not reply:
                continue
            msg = json.loads(reply)
            if msg.get("id") == 9001:
                out = msg.get("result", {}).get("outcome", {})
                decision = out.get("outcome")
                oid = out.get("optionId")
                # 真实 Agent 只认自己给出的 optionId；编的一律视为协议违规
                known = ("allow-once","allow-always","reject-once")
                if decision == "selected" and oid not in known:
                    decision = "PROTOCOL_VIOLATION:" + str(oid)
                elif decision == "selected":
                    decision = decision + ":" + str(oid)
                break
        w({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":sid,
           "update":{"sessionUpdate":"agent_message_chunk",
                     "content":{"type":"text","text":"权限结果=" + str(decision)}}}})
        w({"jsonrpc":"2.0","id":i,"result":{"stopReason":"end_turn"}})
    else:
        w({"jsonrpc":"2.0","id":i,"error":{"code":-32601,"message":"nope"}})
'''

# 反向发一个我们不支持的方法：必须明确回错误，不能装死
_NOSY_AGENT = '''
import json, sys
def w(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    if req.get("method") == "initialize":
        w({"jsonrpc":"2.0","id":5555,"method":"fs/readTextFile",
           "params":{"path":"C:/Windows/System32/config/SAM"}})
        for reply in sys.stdin:
            if not reply.strip():
                continue
            msg = json.loads(reply)
            if msg.get("id") == 5555:
                w({"jsonrpc":"2.0","id":req.get("id"),
                   "result":{"protocolVersion":1,"_reply":msg.get("error",{}).get("code")}})
                break
'''

_CRASH_AGENT = '''
import sys
sys.stderr.write("fatal: agent binary missing\\n")
sys.exit(2)
'''


def _write_agent(tmp_path: Path, source: str, name: str) -> str:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return f'"{sys.executable}" "{path}"'


def _env(command_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "desktop", "id": "ui"},
        "source": "ui",
        "workspaceId": "ws-local",
        "requestedAt": datetime.now(UTC).isoformat(),
        "payload": payload or {},
    }


def _new_db(tmp_path: Path) -> tuple[sqlite3.Connection, Repos]:
    conn = connect(str(tmp_path / "acp.db"))
    run_migrations(conn, _MIG_DIR)
    return conn, Repos(conn)


def _run(raw: dict[str, Any], deps: Deps) -> dict[str, Any]:
    return asyncio.run(dispatch(raw, deps))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """把 STEPWORK_HOME 指到临时目录，避免污染真实素材目录。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path / "home"))
    yield
    # 活跃会话是模块级单例，泄漏会连带泄漏子进程并污染后续用例
    for session in list(acp._SESSIONS.values()):
        with contextlib.suppress(Exception):
            asyncio.run(session.close())
    acp._SESSIONS.clear()


# --------------------------------------------------------------------------
# 传输层：双向
# --------------------------------------------------------------------------


async def test_handshake_returns_agent_info(tmp_path: Path) -> None:
    cmd = _write_agent(tmp_path, _AGENT, "agent.py")
    session = AcpSession(acp._argv(cmd))
    try:
        info = await session.start()
        assert info["agentInfo"]["name"] == "fake-acp"
    finally:
        await session.close()


async def test_streaming_updates_reach_handler(tmp_path: Path) -> None:
    """流式进度必须经回调实时到手，而不是等 prompt 返回才一次性给。"""
    cmd = _write_agent(tmp_path, _AGENT, "agent.py")
    seen: list[dict[str, Any]] = []

    async def on_update(params: dict[str, Any]) -> None:
        seen.append(params)

    async def allow(_params: dict[str, Any]) -> bool:
        return True

    session = AcpSession(
        acp._argv(cmd), cwd=str(tmp_path), on_update=on_update, on_permission=allow
    )
    try:
        await session.start()
        await session.new_session(str(tmp_path))
        result = await session.prompt("分析这个素材")
        assert result["stopReason"] == "end_turn"
    finally:
        await session.close()

    assert len(seen) >= 3, seen
    text = summarize_updates(seen)
    assert "正在分析" in text and "素材" in text
    # optionId 必须取自 Agent 给出的 options；编一个会被判协议违规
    assert "PROTOCOL_VIOLATION" not in text, text
    # 允许时优先选 allow_once，绝不替用户选 allow_always（那等于长期授权）
    assert "权限结果=selected:allow-once" in text, text


async def test_permission_denied_is_conveyed(tmp_path: Path) -> None:
    """拒绝要走 ``selected`` + reject_once 选项，**不是** ``cancelled``。

    协议里 ``cancelled`` 的语义是「整轮对话被取消」。用它表示「这次操作被
    拒绝」会让真实 Agent 以为用户中止了整轮，直接停掉后续工作。
    """
    cmd = _write_agent(tmp_path, _AGENT, "agent.py")

    async def deny(_params: dict[str, Any]) -> bool:
        return False

    session = AcpSession(acp._argv(cmd), cwd=str(tmp_path), on_permission=deny)
    try:
        await session.start()
        await session.new_session(str(tmp_path))
        await session.prompt("删点东西")
    finally:
        await session.close()
    text = summarize_updates(session.updates)
    assert "权限结果=selected:reject-once" in text, text
    assert "PROTOCOL_VIOLATION" not in text


async def test_no_permission_handler_defaults_to_deny(tmp_path: Path) -> None:
    """没装处理器就默认拒绝 —— 失败必须是安全的那一侧。"""
    cmd = _write_agent(tmp_path, _AGENT, "agent.py")
    session = AcpSession(acp._argv(cmd), cwd=str(tmp_path))
    try:
        await session.start()
        await session.new_session(str(tmp_path))
        await session.prompt("删点东西")
    finally:
        await session.close()
    assert "权限结果=selected:reject-once" in summarize_updates(session.updates)


async def test_unsupported_reverse_request_gets_error(tmp_path: Path) -> None:
    """Agent 反向要读任意文件：我们不实现该方法，必须明确回错误。

    装死会让 Agent 一直阻塞，看起来像「卡住」而不是「被拒绝」。
    """
    cmd = _write_agent(tmp_path, _NOSY_AGENT, "nosy.py")
    session = AcpSession(acp._argv(cmd), cwd=str(tmp_path))
    try:
        info = await session.start()
        assert info["_reply"] == -32601
    finally:
        await session.close()


async def test_crash_is_typed(tmp_path: Path) -> None:
    cmd = _write_agent(tmp_path, _CRASH_AGENT, "crash.py")
    session = AcpSession(acp._argv(cmd))
    try:
        with pytest.raises(AcpClientError) as e:
            await session.start()
        assert e.value.code in {"ACP_EOF", "ACP_TIMEOUT"}
    finally:
        await session.close()


async def test_spawn_failure_is_typed() -> None:
    session = AcpSession(["definitely-not-a-real-agent-xyz"])
    with pytest.raises(AcpClientError) as e:
        await session.start()
    assert e.value.code == "ACP_SPAWN_FAILED"


# --------------------------------------------------------------------------
# 命令层
# --------------------------------------------------------------------------


def test_add_acp_agent_probes_then_persists(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        cmd = _write_agent(tmp_path, _AGENT, "agent.py")
        res = _run(_env("AddAcpAgent", {"command": cmd}), Deps(repos=repos))
        assert res["ok"] is True, res
        assert res["detail"]["agent_info"]["name"] == "fake-acp"
        row = conn.execute("SELECT * FROM agent_connections").fetchone()
        assert row["protocol"] == "acp-client"
        assert row["local_or_remote"] == "local"
    finally:
        conn.close()


def test_add_acp_agent_does_not_persist_crashed(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        cmd = _write_agent(tmp_path, _CRASH_AGENT, "crash.py")
        res = _run(_env("AddAcpAgent", {"command": cmd}), Deps(repos=repos))
        assert res["ok"] is False
        assert conn.execute("SELECT COUNT(*) n FROM agent_connections").fetchone()["n"] == 0
    finally:
        conn.close()


async def test_session_is_bound_to_project_and_scoped(tmp_path: Path) -> None:
    """§13.6：Session 绑定 Project，且 Root 只到项目目录。

    用 ``async def`` 而不是 ``asyncio.run`` 逐条跑：ACP 会话是长连接，
    绑在创建它的事件循环上（子进程管道挂在该循环的 transport）。生产里
    worker 全程只有一个循环，这里必须照样只有一个，否则测的就不是真实形态。
    """
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        cmd = _write_agent(tmp_path, _AGENT, "agent.py")
        cid = (await dispatch(_env("AddAcpAgent", {"command": cmd}), deps))["detail"][
            "connection_id"
        ]
        pid = (await dispatch(_env("CreateProject", {"title": "ACP"}), deps))["detail"][
            "project"
        ]["id"]

        res = await dispatch(
            _env("StartAcpSession", {"connectionId": cid, "projectId": pid}), deps
        )
        assert res["ok"] is True, res
        root = res["detail"]["root"]
        # Root 必须落在项目自己的目录里，不是 workspace 根
        assert pid in root
        # noqa 理由：测试里一次性 stat，不是会阻塞事件循环的真实 IO
        assert Path(root).is_dir()  # noqa: ASYNC240

        row = conn.execute("SELECT * FROM agent_sessions").fetchone()
        assert row["project_id"] == pid
        assert row["agent_connection_id"] == cid
        assert row["status"] == "active"
        assert row["external_session_id"] == "sess-1"

        await dispatch(
            _env("EndAcpSession", {"sessionId": res["detail"]["session_id"]}), deps
        )
    finally:
        conn.close()


async def test_prompt_streams_and_files_permission_for_review(tmp_path: Path) -> None:
    """权限请求必须落 Approval Center 且本轮被拒（PRD-AGT-008 / §13.6）。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        cmd = _write_agent(tmp_path, _AGENT, "agent.py")
        cid = (await dispatch(_env("AddAcpAgent", {"command": cmd}), deps))["detail"][
            "connection_id"
        ]
        pid = (await dispatch(_env("CreateProject", {"title": "ACP"}), deps))["detail"][
            "project"
        ]["id"]
        sid = (
            await dispatch(
                _env("StartAcpSession", {"connectionId": cid, "projectId": pid}), deps
            )
        )["detail"]["session_id"]

        res = await dispatch(
            _env("SendAcpPrompt", {"sessionId": sid, "text": "帮我清理"}), deps
        )
        assert res["ok"] is True, res
        assert res["detail"]["stop_reason"] == "end_turn"
        assert "正在分析" in res["detail"]["text"]
        # 未经人工批准 → Agent 收到的是「明确拒绝」，而不是「整轮取消」
        assert "权限结果=selected:reject-once" in res["detail"]["text"]
        assert res["detail"]["pending_approvals"] == 1

        approval = conn.execute(
            "SELECT * FROM approval_requests WHERE status='pending'"
        ).fetchone()
        assert approval["action_type"] == "AcpToolCall"
        assert approval["target"] == "删除项目文件"
        assert approval["actor"] == f"acp:{cid}"

        # 留痕：一轮 prompt 一条 task + 一条 artifact，信任等级同其它外部来源
        task = conn.execute(
            "SELECT * FROM agent_tasks WHERE id=?", (res["detail"]["agent_task_id"],)
        ).fetchone()
        assert task["task_type"] == "acp:prompt"
        art = conn.execute(
            "SELECT * FROM agent_artifacts WHERE agent_task_id=?", (task["id"],)
        ).fetchone()
        assert art["trust_level"] == "external-unverified"
        assert art["review_state"] == "pending_review"
        # 外部内容绝不自动进正文
        assert conn.execute("SELECT COUNT(*) n FROM content_versions").fetchone()["n"] == 0

        await dispatch(_env("EndAcpSession", {"sessionId": sid}), deps)
    finally:
        conn.close()


def test_prompt_on_dead_session_is_rejected(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        res = _run(
            _env("SendAcpPrompt", {"sessionId": "asess_nope", "text": "x"}),
            Deps(repos=repos),
        )
        assert res["ok"] is False
        assert str(res["error"]).startswith("NOT_FOUND")
    finally:
        conn.close()


async def test_list_sessions_marks_dead_ones(tmp_path: Path) -> None:
    """worker 重启后库里仍是 active，但进程没了 —— 要如实标出。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        cmd = _write_agent(tmp_path, _AGENT, "agent.py")
        cid = (await dispatch(_env("AddAcpAgent", {"command": cmd}), deps))["detail"][
            "connection_id"
        ]
        pid = (await dispatch(_env("CreateProject", {"title": "ACP"}), deps))["detail"][
            "project"
        ]["id"]
        sid = (
            await dispatch(
                _env("StartAcpSession", {"connectionId": cid, "projectId": pid}), deps
            )
        )["detail"]["session_id"]

        listed = (await dispatch(_env("ListAcpSessions"), deps))["detail"]["sessions"]
        assert listed[0]["live"] is True

        # 模拟 worker 重启：进程注册表没了，库里仍是 active
        for stale in list(acp._SESSIONS.values()):
            await stale.close()
        acp._SESSIONS.clear()
        listed = (await dispatch(_env("ListAcpSessions"), deps))["detail"]["sessions"]
        assert listed[0]["live"] is False
        assert listed[0]["status"] == "active"
        assert listed[0]["id"] == sid
    finally:
        conn.close()


def test_mcp_connection_cannot_be_used_as_acp(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO agent_connections (id, protocol, endpoint_or_command, "
            "local_or_remote, trust_level, auth_ref, status, capabilities, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("mcpc_x", "mcp-client", "cmd", "local", "external-unverified",
             None, "active", "[]", now, now),
        )
        conn.commit()
        deps = Deps(repos=repos)
        pid = _run(_env("CreateProject", {"title": "P"}), deps)["detail"]["project"]["id"]
        res = _run(
            _env("StartAcpSession", {"connectionId": "mcpc_x", "projectId": pid}), deps
        )
        assert res["ok"] is False
        assert str(res["error"]).startswith("INVALID_ARGUMENT")
    finally:
        conn.close()


def test_external_agents_cannot_start_local_agents(tmp_path: Path) -> None:
    """外部 Agent 不得借 STEPWORK 拉起本地进程（最危险的跳板路径）。"""
    conn, repos = _new_db(tmp_path)
    try:
        raw = _env("AddAcpAgent", {"command": "whatever"})
        raw["actor"] = {"type": "agent", "id": "evil"}
        raw["source"] = "acp"
        res = _run(raw, Deps(repos=repos))
        assert res["ok"] is False
        assert str(res["error"]).startswith("FORBIDDEN_ACTOR"), res
        assert conn.execute("SELECT COUNT(*) n FROM agent_connections").fetchone()["n"] == 0
    finally:
        conn.close()


# ----- 权限 outcome 构造（对齐 agentclientprotocol.com 的 schema） -----


def test_outcome_picks_option_id_from_agent_options() -> None:
    """optionId 必须来自对方给的 options，不能自己编。"""
    params = {
        "options": [
            {"optionId": "yes-1", "name": "允许一次", "kind": "allow_once"},
            {"optionId": "no-1", "name": "拒绝", "kind": "reject_once"},
        ]
    }
    assert build_permission_outcome(params, True) == {
        "outcome": "selected",
        "optionId": "yes-1",
    }
    assert build_permission_outcome(params, False) == {
        "outcome": "selected",
        "optionId": "no-1",
    }


def test_outcome_prefers_once_over_always() -> None:
    """绝不替用户选 *_always —— 那等于替他做了长期授权。"""
    params = {
        "options": [
            {"optionId": "always", "kind": "allow_always"},
            {"optionId": "once", "kind": "allow_once"},
            {"optionId": "rej-always", "kind": "reject_always"},
            {"optionId": "rej-once", "kind": "reject_once"},
        ]
    }
    assert build_permission_outcome(params, True)["optionId"] == "once"
    assert build_permission_outcome(params, False)["optionId"] == "rej-once"


def test_outcome_falls_back_to_always_when_only_choice() -> None:
    """对方只给了 always 选项时才用它 —— 否则无法表达意图。"""
    params = {"options": [{"optionId": "aa", "kind": "allow_always"}]}
    assert build_permission_outcome(params, True)["optionId"] == "aa"


def test_outcome_cancelled_only_when_no_matching_option() -> None:
    """没有能表达该意图的选项时才回 cancelled（此时确实没得选）。"""
    only_allow = {"options": [{"optionId": "a", "kind": "allow_once"}]}
    assert build_permission_outcome(only_allow, False) == {"outcome": "cancelled"}
    assert build_permission_outcome({}, True) == {"outcome": "cancelled"}
    # 缺 optionId 的坏选项要跳过，不能构造出没有 optionId 的 selected
    broken = {"options": [{"kind": "allow_once"}]}
    assert build_permission_outcome(broken, True) == {"outcome": "cancelled"}
