"""A2A 互操作测试（PRD-AGT-005）。

主线是**自环回**：拉起我们自己的 A2A Server，再用我们自己的 A2A Client
去连它 —— 发现 Agent Card、映射 Skill、发任务、拿 Artifact 全链路都用
真 HTTP，不 mock 传输。这样 Server 与 Client 任一侧写错都会被抓到，
而不是两边各自对着 mock 自说自话。

另外覆盖安全面：Skill 白名单（Publisher Execute 不可达）、令牌鉴权、
URL scheme 校验、停用连接被拒。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from worker.runtime.agents import a2a_server
from worker.runtime.agents.a2a_card import (
    SKILL_COMMANDS,
    SKILLS,
    build_agent_card,
    parse_remote_card,
    resolve_skill_command,
)
from worker.runtime.agents.a2a_client import (
    A2aClientError,
    extract_artifact_text,
    normalize_base_url,
)
from worker.runtime.commands.bus import _ROUTES, dispatch
from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


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


class _FakeAI:
    """最小 AI provider 桩：让 AnalyzeSource 能跑通到产出 Artifact。

    A2A Server 在自己的线程里另开 DB 连接，但 provider 是跨线程共用的
    （见 handlers/a2a._thread_deps），所以这里注入一次即可覆盖两侧。
    """

    name = "fake-ai"
    model = "fake-1"
    estimated_cost_per_1k = 0.0

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "summary": "摘要", "topics": ["a"], "sentiment": "neutral",
            "suggested_title": None, "suggested_tags": [], "key_points": [],
            "target_audience": None, "hook": "钩子", "structure": [], "risks": [],
            "provider": "fake-ai", "model": "fake-1", "confidence": 0.9,
        }


def _new_db(tmp_path: Path) -> tuple[sqlite3.Connection, Repos]:
    conn = connect(str(tmp_path / "a2a.db"))
    run_migrations(conn, _MIG_DIR)
    return conn, Repos(conn)


def _run(raw: dict[str, Any], deps: Deps) -> dict[str, Any]:
    return asyncio.run(dispatch(raw, deps))


def _wait_ready(card_url: str, timeout: float = 5.0) -> None:
    """等 Server 真的能应答再断言。

    ``ThreadingHTTPServer`` 在构造时就 bind+listen，理论上返回即可连；但
    Windows 上全量跑时偶发过一次瞬时连接失败（端口 TIME_WAIT 复用等），
    表现为随机红。这里等的是「能应答」而不是 sleep 固定时长，所以不会
    掩盖真正的启动失败 —— 起不来照样在 5s 后报错。
    """
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            httpx.get(card_url, timeout=2).raise_for_status()
            return
        except Exception as e:  # noqa: BLE001 - 就绪前的失败是预期的
            last = e
            time.sleep(0.05)
    raise AssertionError(f"A2A Server 在 {timeout}s 内未就绪：{last}")


@pytest.fixture(autouse=True)
def _always_stop_server() -> Any:
    """每个用例后都停掉 Server —— 它是进程级单例，泄漏会污染后续用例。

    停机会经 stop hook 一并释放 handler 侧的线程级 DB 连接（见
    ``handlers.a2a.reset_thread_deps``），否则下个用例的 Server 线程会
    复用指向**已关闭库**的连接。
    """
    yield
    a2a_server.stop()


# --------------------------------------------------------------------------
# Agent Card 契约
# --------------------------------------------------------------------------


def test_card_lists_the_six_spec_skills() -> None:
    """SYSTEM_SPEC §13.5 定义的首批 6 个 Skill 必须都在卡片里。"""
    card = build_agent_card("http://127.0.0.1:9999")
    ids = [s["id"] for s in card["skills"]]
    assert ids == [
        "content-reference-analysis",
        "original-topic-proposal",
        "script-drafting",
        "brand-voice-rewriting",
        "media-draft-rendering",
        "publish-preparation",
    ]
    assert card["url"] == "http://127.0.0.1:9999"
    # 不做流式就不能报 streaming=true，否则对端会一直等 SSE
    assert card["capabilities"]["streaming"] is False


def test_every_skill_maps_to_a_real_command() -> None:
    """Skill 映射的命令必须真在 bus 路由表里，否则调用必然 404。"""
    for skill in SKILLS:
        assert skill.command in _ROUTES, f"{skill.id} → {skill.command} 不在 _ROUTES"


def test_publisher_execute_is_not_reachable() -> None:
    """§13.5：A2A Server 默认不暴露 Publisher Execute。"""
    commands = set(SKILL_COMMANDS.values())
    # 发布准备只能到「生成填充包」，不能到任何执行发布的命令（ADR-008）
    assert "BuildPlatformFillPackage" in commands
    for forbidden in ("RecordPublishResult", "RequestPublishAuthorization"):
        assert forbidden not in commands
    assert resolve_skill_command("publisher-execute") is None
    assert resolve_skill_command("") is None


def test_parse_remote_card_tolerates_garbage() -> None:
    """对方的 Card 是不可信输入，坏结构不能把我们打崩。"""
    name, skills = parse_remote_card(
        {"name": 123, "skills": ["not-a-dict", {"noid": 1}, {"id": "ok", "name": None}]}
    )
    assert name == "123"
    assert [s["id"] for s in skills] == ["ok"]
    assert parse_remote_card({}) == ("unknown-agent", [])


def test_normalize_base_url_rejects_non_http() -> None:
    """挡住 file:// —— 否则 urljoin 能拼出本地文件读取。"""
    for bad in ("file:///etc/passwd", "ftp://x.test", "  "):
        with pytest.raises(A2aClientError):
            normalize_base_url(bad)
    assert normalize_base_url("example.test") == "http://example.test"
    assert normalize_base_url("https://a.test/") == "https://a.test"


def test_extract_artifact_text_handles_both_shapes() -> None:
    """A2A 结果可能是 Task（artifacts）或 Message（parts），两种都要能取。"""
    task = {"artifacts": [{"parts": [{"kind": "text", "text": "甲"}]}]}
    assert extract_artifact_text(task) == "甲"
    msg = {"parts": [{"kind": "text", "text": "乙"}, {"kind": "file"}]}
    assert extract_artifact_text(msg) == "乙\n[file]"
    assert extract_artifact_text({}) == ""


# --------------------------------------------------------------------------
# Server（入站）
# --------------------------------------------------------------------------


def test_server_serves_card_without_auth(tmp_path: Path) -> None:
    """能力发现是公开握手第一步，卡片不含敏感数据，故不鉴权。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        res = _run(_env("StartA2aServer"), deps)
        assert res["ok"] is True, res
        url = res["detail"]["card_url"]
        _wait_ready(url)
        card = httpx.get(url, timeout=10).json()
        assert card["name"] == "STEPWORK"
        assert len(card["skills"]) == 6
    finally:
        conn.close()


def test_server_rejects_unauthenticated_post(tmp_path: Path) -> None:
    """POST 是能力面，必须带令牌 —— 本机其它进程正是 §9.1 的威胁模型。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        res = _run(_env("StartA2aServer"), deps)
        url = res["detail"]["url"]
        _wait_ready(res["detail"]["card_url"])
        r = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "message/send"}, timeout=10)
        assert r.status_code == 401
    finally:
        conn.close()


def test_server_rejects_unknown_skill(tmp_path: Path) -> None:
    """白名单之外的 Skill 一律拒绝（Publisher Execute 的挡板）。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        res = _run(_env("StartA2aServer"), deps)
        url, token = res["detail"]["url"], res["detail"]["token"]
        _wait_ready(res["detail"]["card_url"])
        r = httpx.post(
            url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "message/send",
                "params": {
                    "message": {"role": "user", "parts": [{"kind": "text", "text": "x"}]},
                    "metadata": {"skillId": "publisher-execute"},
                },
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert r.status_code == 200
        assert "error" in r.json()
        assert "unsupported skill" in r.json()["error"]["message"]
    finally:
        conn.close()


def test_server_rejects_unknown_method(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        res = _run(_env("StartA2aServer"), deps)
        url, token = res["detail"]["url"], res["detail"]["token"]
        _wait_ready(res["detail"]["card_url"])
        r = httpx.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tasks/cancel", "params": {}},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        assert r.json()["error"]["code"] == -32601
    finally:
        conn.close()


def test_server_routes_skill_through_command_bus(tmp_path: Path) -> None:
    """Skill 请求走同一条总线 —— 于是默认拒绝清单/审批降级自动生效。

    ``GenerateScript`` 不在外部 Agent 允许清单里，因此这条请求应被
    §9.1 降级为审批任务而**不是**直接执行。这正是「A2A 不自建权限」的
    证据：我们没在 A2A 层写任何权限判断，保护依然生效。
    """
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        res = _run(_env("StartA2aServer"), deps)
        url, token = res["detail"]["url"], res["detail"]["token"]
        _wait_ready(res["detail"]["card_url"])
        r = httpx.post(
            url,
            json={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "message/send",
                "params": {
                    "message": {"role": "user", "parts": [{"kind": "text", "text": "写个脚本"}]},
                    "metadata": {"skillId": "script-drafting"},
                },
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        body = r.json()
        assert body["id"] == 7
        result = body["result"]
        assert result["kind"] == "task"
        # 被总线拦下 → failed，且理由是 FORBIDDEN_ACTOR（不是执行出错）
        assert result["status"]["state"] == "failed"
        assert "FORBIDDEN_ACTOR" in str(result["metadata"]["error"])
        # 降级产生的审批任务应已入库
        pending = conn.execute(
            "SELECT COUNT(*) n FROM approval_requests WHERE status='pending'"
        ).fetchone()["n"]
        assert pending >= 1
    finally:
        conn.close()


def test_start_is_idempotent_and_stop_reports(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        first = _run(_env("StartA2aServer"), deps)["detail"]["url"]
        second = _run(_env("StartA2aServer"), deps)["detail"]["url"]
        assert first == second, "重复启动不该换端口"
        assert _run(_env("GetA2aServerStatus"), deps)["detail"]["running"] is True
        assert _run(_env("StopA2aServer"), deps)["detail"]["stopped"] is True
        assert _run(_env("GetA2aServerStatus"), deps)["detail"]["running"] is False
        # 已停后再停：如实返回 False，不假装成功
        assert _run(_env("StopA2aServer"), deps)["detail"]["stopped"] is False
    finally:
        conn.close()


def test_server_does_not_listen_by_default() -> None:
    """SYSTEM_SPEC §8.2：用户不开就没有端口。"""
    assert a2a_server.STATE.running is False
    assert a2a_server.STATE.base_url == ""


# --------------------------------------------------------------------------
# Client（出站）—— 自环回：用我们的 Client 连我们的 Server
# --------------------------------------------------------------------------


def test_client_discovers_own_server_card(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        started = _run(_env("StartA2aServer"), deps)["detail"]
        url = started["url"]
        _wait_ready(started["card_url"])

        res = _run(_env("AddA2aAgent", {"url": url}), deps)
        assert res["ok"] is True, res
        assert res["detail"]["agent_name"] == "STEPWORK"
        assert len(res["detail"]["skills"]) == 6

        cid = res["detail"]["connection_id"]
        row = conn.execute(
            "SELECT * FROM agent_connections WHERE id=?", (cid,)
        ).fetchone()
        assert row["protocol"] == "a2a-client"
        assert row["local_or_remote"] == "remote"
        assert row["trust_level"] == "external-unverified"
        # Agent Card → AgentConnection + AgentCapability（§13.5）
        caps = conn.execute(
            "SELECT capability_key FROM agent_capabilities WHERE agent_connection_id=?",
            (cid,),
        ).fetchall()
        assert len(caps) == 6
        assert "script-drafting" in {c["capability_key"] for c in caps}
    finally:
        conn.close()


def test_add_a2a_agent_fails_on_unreachable(tmp_path: Path) -> None:
    """连不上就不落库（与 AddMcpServer 同样的取舍）。"""
    conn, repos = _new_db(tmp_path)
    try:
        # 127.0.0.1:1 基本不可能有服务在听
        res = _run(_env("AddA2aAgent", {"url": "http://127.0.0.1:1"}), Deps(repos=repos))
        assert res["ok"] is False
        assert conn.execute("SELECT COUNT(*) n FROM agent_connections").fetchone()["n"] == 0
    finally:
        conn.close()


def test_call_skill_roundtrip_records_artifact(tmp_path: Path) -> None:
    """自环回全链路 + PRD-AGT-003 留痕。

    用 ``content-reference-analysis``（映射 AnalyzeSource，在外部 Agent
    允许清单内），所以这次会真执行到底并产出 Artifact。
    """
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos, ai=_FakeAI())
        started = _run(_env("StartA2aServer"), deps)["detail"]
        url, token = started["url"], started["token"]
        _wait_ready(started["card_url"])
        cid = _run(_env("AddA2aAgent", {"url": url, "token": token}), deps)["detail"][
            "connection_id"
        ]
        pid = _run(_env("CreateProject", {"title": "A2A"}), deps)["detail"]["project"]["id"]

        res = _run(
            _env(
                "CallA2aSkill",
                {
                    "connectionId": cid,
                    "skillId": "content-reference-analysis",
                    "text": "这是一段用于分析的参考文案，讲的是如何做本地优先的工具。",
                },
                project_id=pid,
            ),
            deps,
        )
        assert res["ok"] is True, (res["error"], res["detail"])
        assert res["detail"]["remote_state"] == "completed", res["detail"]
        assert res["detail"]["trust_level"] == "external-unverified"
        assert res["detail"]["review_state"] == "pending_review"

        task = conn.execute(
            "SELECT * FROM agent_tasks WHERE id=?", (res["detail"]["agent_task_id"],)
        ).fetchone()
        assert task["task_type"] == "a2a:content-reference-analysis"
        art = conn.execute(
            "SELECT * FROM agent_artifacts WHERE agent_task_id=?", (task["id"],)
        ).fetchone()
        assert art["trust_level"] == "external-unverified"
        assert art["review_state"] == "pending_review"
        # 远端返回的内容不自动进正文
        stored = json.loads(art["content_uri_or_json"])
        assert stored["skill"] == "content-reference-analysis"
    finally:
        conn.close()


def test_disabled_a2a_connection_is_rejected(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        started = _run(_env("StartA2aServer"), deps)["detail"]
        url = started["url"]
        _wait_ready(started["card_url"])
        cid = _run(_env("AddA2aAgent", {"url": url}), deps)["detail"]["connection_id"]
        _run(
            _env("SetAgentConnectionStatus", {"connectionId": cid, "status": "inactive"}),
            deps,
        )
        res = _run(
            _env("CallA2aSkill", {"connectionId": cid, "skillId": "script-drafting"}), deps
        )
        assert res["ok"] is False
        assert str(res["error"]).startswith("CONNECTION_DISABLED")
    finally:
        conn.close()


def test_mcp_connection_cannot_be_called_as_a2a(tmp_path: Path) -> None:
    """协议串用必须被挡（同 mcp_client 侧的对称检查）。"""
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
        res = _run(
            _env("CallA2aSkill", {"connectionId": "mcpc_x", "skillId": "script-drafting"}),
            Deps(repos=repos),
        )
        assert res["ok"] is False
        assert str(res["error"]).startswith("INVALID_ARGUMENT")
    finally:
        conn.close()


def test_external_agents_cannot_control_a2a_server(tmp_path: Path) -> None:
    """外部 Agent 不得自行开关 A2A Server 或添加新的对端。"""
    conn, repos = _new_db(tmp_path)
    try:
        for command in ("StartA2aServer", "AddA2aAgent"):
            raw = _env(command, {"url": "http://127.0.0.1:1"})
            raw["actor"] = {"type": "agent", "id": "evil"}
            raw["source"] = "a2a"
            res = _run(raw, Deps(repos=repos))
            assert res["ok"] is False, (command, res)
            assert str(res["error"]).startswith("FORBIDDEN_ACTOR"), res
        assert a2a_server.STATE.running is False
    finally:
        conn.close()


def test_remote_token_is_never_persisted(tmp_path: Path) -> None:
    """对端令牌只在内存 —— 与设置页「API Key 绝不落盘」同一条原则。

    翻的是**库里真正写下去的字节**，而不是「有没有调用某个函数」。
    """
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        started = _run(_env("StartA2aServer"), deps)["detail"]
        _wait_ready(started["card_url"])
        res = _run(
            _env("AddA2aAgent", {"url": started["url"], "token": started["token"]}), deps
        )
        assert res["ok"] is True, res
        assert res["detail"]["token_persisted"] is False

        row = conn.execute(
            "SELECT * FROM agent_connections WHERE id=?",
            (res["detail"]["connection_id"],),
        ).fetchone()
        assert row["auth_ref"] is None
        blob = " ".join(str(row[k]) for k in row.keys())
        assert started["token"] not in blob
        # 能力表里也不能夹带
        caps = conn.execute("SELECT * FROM agent_capabilities").fetchall()
        for cap in caps:
            assert started["token"] not in " ".join(str(cap[k]) for k in cap.keys())
    finally:
        conn.close()


def test_call_without_token_surfaces_auth_error(tmp_path: Path) -> None:
    """没令牌就该拿到明确的鉴权错误，而不是静默成功或神秘失败。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        started = _run(_env("StartA2aServer"), deps)["detail"]
        url = started["url"]
        _wait_ready(started["card_url"])
        # 不传 token 注册（Card 本就不鉴权，注册会成功）
        cid = _run(_env("AddA2aAgent", {"url": url}), deps)["detail"]["connection_id"]
        res = _run(
            _env("CallA2aSkill", {"connectionId": cid, "skillId": "script-drafting"}), deps
        )
        assert res["ok"] is False
        assert "401" in str(res["error"])
    finally:
        conn.close()


def test_stop_releases_thread_connections(tmp_path: Path) -> None:
    """停机必须释放线程级 DB 连接。

    这些连接由 Server 线程按线程缓存；只在 StopA2aServer 命令分支里清会
    漏掉其它停机路径（进程退出、测试收尾），重启后就会复用指向旧库的连接
    —— 而那个库可能已被换掉（RestoreWorkspace 会整体替换 DB）。
    """
    from worker.runtime.handlers import a2a as a2a_handler

    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        started = _run(_env("StartA2aServer"), deps)["detail"]
        _wait_ready(started["card_url"])
        # 打一次能力面，逼 Server 线程建出线程级连接
        httpx.post(
            started["url"],
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "message/send",
                "params": {
                    "message": {"role": "user", "parts": [{"kind": "text", "text": "x"}]},
                    "metadata": {"skillId": "content-reference-analysis"},
                },
            },
            headers={"Authorization": f"Bearer {started['token']}"},
            timeout=15,
        )
        assert a2a_handler._THREAD_DEPS, "Server 线程应已建立自己的连接"

        # 直接调 stop()（不走命令），验证 hook 生效
        a2a_server.stop()
        assert not a2a_handler._THREAD_DEPS, "停机后线程连接应已释放"
    finally:
        conn.close()
