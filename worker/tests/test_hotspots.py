"""热点命令端到端（S5：DiscoverHotspots / RecommendHotspots / 反馈闭环）。

与 ``test_mcp_client.py`` 同一套路：**真子进程跑一个最小 MCP Server**，
不 mock 传输层 —— 这条链路的坑全在进程/管道上，mock 掉就什么也没测。

覆盖的不只是「跑通」，而是三条产品纪律：

1. 源失败要进 ``errors`` 而不是让整个命令失败，也不是静默少给数据；
2. 未配 AI 时理由降级为规则模板，且 ``reasonSource`` **如实**写 ``rule``；
3. 反馈能改变下一轮推荐（否则「推荐机制」只是个排序函数）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"

# --------------------------------------------------------------------------
# 最小热点 MCP Server：返回固定的两个源 + 四条热点
# --------------------------------------------------------------------------

_SERVER = '''
import json, sys

ITEMS = [
  {"id":"i1","source":"toutiao_hot","title":"秋天穿搭的五个技巧","url":"u1",
   "summary":"换季穿搭","publishedAt":"2026-09-10T02:00:00+00:00","score":900,"meta":{}},
  {"id":"i2","source":"toutiao_hot","title":"露营装备清单","url":"u2",
   "summary":"户外","publishedAt":"2026-09-10T01:00:00+00:00","score":800,"meta":{}},
  {"id":"i3","source":"github_trending","title":"AI 工具评测框架","url":"u3",
   "summary":"开源项目","publishedAt":"2026-09-10T03:00:00+00:00","score":700,"meta":{}},
  {"id":"i4","source":"github_trending","title":"家人们冲鸭爆款","url":"u4",
   "summary":"禁用词测试","publishedAt":"2026-09-10T03:30:00+00:00","score":600,
   "meta":{"parse":"text-fallback"}},
]
SOURCES = [
  {"id":"toutiao_hot","title":"头条","kind":"api","needsKey":False,
   "requiresLogin":False,"note":""},
  {"id":"douhot","title":"热点宝","kind":"browser","needsKey":False,
   "requiresLogin":True,"note":"需登录"},
]

def w(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()

for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    m, i = req.get("method"), req.get("id")
    if m == "initialize":
        w({"jsonrpc":"2.0","id":i,"result":{"protocolVersion":"2024-11-05",
           "capabilities":{"tools":{}},
           "serverInfo":{"name":"stepwork-hotspot-mcp","version":"0.1.0"}}})
    elif m == "tools/list":
        w({"jsonrpc":"2.0","id":i,"result":{"tools":[
            {"name":"discover_hotspots","description":"","inputSchema":{"type":"object"}},
            {"name":"list_sources","description":"","inputSchema":{"type":"object"}}]}})
    elif m == "tools/call":
        p = req.get("params") or {}
        name = p.get("name")
        if name == "list_sources":
            out = {"sources": SOURCES}
        elif name == "discover_hotspots":
            args = p.get("arguments") or {}
            limit = int(args.get("limit") or 20)
            out = {"items": ITEMS[:limit], "errors": [], "count": min(limit, 4),
                   "sources": ["toutiao_hot", "github_trending"],
                   "skipped": [{"source": "douhot", "reason": "需要登录态"}]}
        else:
            w({"jsonrpc":"2.0","id":i,"error":{"code":-32601,"message":"nope"}})
            continue
        w({"jsonrpc":"2.0","id":i,"result":{
            "content":[{"type":"text","text":json.dumps(out, ensure_ascii=False)}],
            "isError": False}})
    else:
        w({"jsonrpc":"2.0","id":i,"result":{}})
'''


def _write_server(tmp_path: Path) -> str:
    path = tmp_path / "stepwork-hotspot-mcp.py"
    path.write_text(_SERVER, encoding="utf-8")
    return f'"{sys.executable}" "{path}"'


class FakeAI:
    """按 id 回一句固定理由；``fail=True`` 时抛错，用于验证降级。"""

    name = "fake-ai"
    model = "fake-1"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def complete(self, prompt: str, schema: Any = None) -> dict[str, Any]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("quota exceeded")
        assert "秋天穿搭" in prompt
        return {"reasons": [{"id": "i3", "reason": "正好在你的内容支柱上"}]}


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
    conn = connect(str(tmp_path / "hot.db"))
    run_migrations(conn, _MIG_DIR)
    # 走 repo 的 ensure（与命令运行时的路径一致），别手搓 INSERT 漏掉必填列
    Repos(conn).workspaces.ensure("ws-local")
    return conn, Repos(conn)


def _add_connection(conn: sqlite3.Connection, command: str, *, conn_id: str = "mcpc_hot") -> str:
    stamp = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO agent_connections "
        "(id, protocol, endpoint_or_command, local_or_remote, trust_level, "
        "auth_ref, status, capabilities, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            conn_id, "mcp-client", command, "local", "external-unverified",
            None, "active", "[]", stamp, stamp,
        ),
    )
    conn.commit()
    return conn_id


def _run(raw: dict[str, Any], deps: Deps) -> dict[str, Any]:
    import asyncio

    result: dict[str, Any] = asyncio.run(dispatch(raw, deps))
    return result


# --------------------------------------------------------------------------
# ListHotspotSources


def test_list_sources_returns_upstream_catalog(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        _add_connection(conn, _write_server(tmp_path))
        res = _run(_env("ListHotspotSources"), Deps(repos=repos))
        assert res["ok"] is True, res
        ids = {s["id"] for s in res["detail"]["sources"]}
        assert ids == {"toutiao_hot", "douhot"}
        # 需登录这件事要在抓之前就暴露，而不是抓完才报错
        douhot = next(s for s in res["detail"]["sources"] if s["id"] == "douhot")
        assert douhot["requiresLogin"] is True
    finally:
        conn.close()


def test_without_connection_error_is_actionable(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        res = _run(_env("DiscoverHotspots"), Deps(repos=repos))
        assert res["ok"] is False
        assert str(res["error"]).startswith("NOT_FOUND")
        # 错误信息要教人怎么修，不能只说「找不到」
        assert "AddMcpServer" in str(res["error"])
    finally:
        conn.close()


def test_ambiguous_connections_require_explicit_id(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        cmd = _write_server(tmp_path)
        _add_connection(conn, cmd, conn_id="a")
        _add_connection(conn, cmd, conn_id="b")
        res = _run(_env("DiscoverHotspots"), Deps(repos=repos))
        assert res["ok"] is False
        assert "connectionId" in str(res["error"])
    finally:
        conn.close()


# --------------------------------------------------------------------------
# DiscoverHotspots


def test_discover_persists_items_and_batch(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        _add_connection(conn, _write_server(tmp_path))
        res = _run(_env("DiscoverHotspots", {"limit": 4}), Deps(repos=repos))
        assert res["ok"] is True, res
        detail = res["detail"]
        assert detail["count"] == 4
        assert detail["saved"] == 4
        assert detail["batch_id"].startswith("hsb_")
        assert set(detail["sources"]) == {"toutiao_hot", "github_trending"}
        # 源失败与「这次没抓」分开：skipped 不是故障
        assert [s["source"] for s in detail["skipped"]] == ["douhot"]
        assert detail["errors"] == []

        rows = conn.execute("SELECT * FROM hotspot_items").fetchall()
        assert len(rows) == 4
        assert {r["source"] for r in rows} == {"toutiao_hot", "github_trending"}
    finally:
        conn.close()


def test_discover_no_save_skips_persistence(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        _add_connection(conn, _write_server(tmp_path))
        res = _run(_env("DiscoverHotspots", {"save": False}), Deps(repos=repos))
        assert res["ok"] is True, res
        assert res["detail"]["count"] == 4
        assert res["detail"]["saved"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM hotspot_items").fetchone()["n"] == 0
    finally:
        conn.close()


def test_discover_rejects_bad_limit(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        _add_connection(conn, _write_server(tmp_path))
        res = _run(_env("DiscoverHotspots", {"limit": "many"}), Deps(repos=repos))
        assert res["ok"] is False
        assert str(res["error"]).startswith("INVALID_ARGUMENT")
    finally:
        conn.close()


# --------------------------------------------------------------------------
# RecommendHotspots


def _discovered(conn: sqlite3.Connection, repos: Repos) -> dict[str, Any]:
    res = _run(_env("DiscoverHotspots", {"limit": 4}), Deps(repos=repos))
    assert res["ok"] is True, res
    detail: dict[str, Any] = res["detail"]
    return detail


def test_recommend_without_ai_falls_back_to_rule_reason(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        _add_connection(conn, _write_server(tmp_path))
        _discovered(conn, repos)
        res = _run(_env("RecommendHotspots", {"limit": 4}), Deps(repos=repos))
        assert res["ok"] is True, res
        detail = res["detail"]
        assert detail["count"] == 4
        assert detail["considered"] == 4
        # 降级必须可见，不能让 UI 把模板句当 AI 洞见
        assert detail["reason_source"] == "rule"
        assert "未配置 AI" in detail["reason_note"]
        assert all(r["reasonSource"] == "rule" for r in detail["recommendations"])
        assert all(r["reason"] for r in detail["recommendations"])
    finally:
        conn.close()


def test_recommend_uses_ai_reason_when_available(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        _add_connection(conn, _write_server(tmp_path))
        _discovered(conn, repos)
        ai = FakeAI()
        res = _run(
            _env("RecommendHotspots", {"limit": 4, "reasonTopN": 4}),
            Deps(repos=repos, ai=ai),
        )
        assert res["ok"] is True, res
        assert res["detail"]["reason_source"] == "ai"
        by_id = {r["id"]: r for r in res["detail"]["recommendations"]}
        assert by_id["i3"]["reasonSource"] == "ai"
        assert by_id["i3"]["reason"] == "正好在你的内容支柱上"
        # 没写理由的走模板，不假装是 AI 写的
        assert by_id["i1"]["reasonSource"] == "rule"
    finally:
        conn.close()


def test_recommend_degrades_when_ai_fails(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        _add_connection(conn, _write_server(tmp_path))
        _discovered(conn, repos)
        res = _run(
            _env("RecommendHotspots"), Deps(repos=repos, ai=FakeAI(fail=True))
        )
        assert res["ok"] is True, res
        assert res["detail"]["reason_source"] == "rule"
        assert "AI 写理由失败" in res["detail"]["reason_note"]
    finally:
        conn.close()


def test_recommend_penalises_banned_expression(tmp_path: Path) -> None:
    """命中品牌禁用词的条目必须垫底（总分 0），且风险里指名是哪个词。"""
    conn, repos = _new_db(tmp_path)
    try:
        _add_connection(conn, _write_server(tmp_path))
        _discovered(conn, repos)
        # 建品牌档并绑到项目
        conn.execute(
            "INSERT INTO brand_profiles (id, workspace_id, name, banned_expressions, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("bp1", "ws-local", "测试档", json.dumps(["家人们"]), "t", "t"),
        )
        conn.execute(
            "INSERT INTO content_projects (id, workspace_id, title, brand_profile_id, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("pj1", "ws-local", "项目", "bp1", "t", "t"),
        )
        conn.commit()

        res = _run(
            _env("RecommendHotspots", {"limit": 4}, project_id="pj1"),
            Deps(repos=repos),
        )
        assert res["ok"] is True, res
        assert res["detail"]["brand_applied"] is True
        recs = res["detail"]["recommendations"]
        banned_row = next(r for r in recs if r["title"] == "家人们冲鸭爆款")
        assert banned_row["score"] == 0
        assert banned_row["breakdown"]["penalty"] == 1.0
        assert any("家人们" in risk for risk in banned_row["risks"])
        assert recs[-1]["title"] == "家人们冲鸭爆款"
    finally:
        conn.close()


def test_recommend_flags_text_fallback_risk(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        _add_connection(conn, _write_server(tmp_path))
        _discovered(conn, repos)
        res = _run(_env("RecommendHotspots", {"limit": 4}), Deps(repos=repos))
        row = next(r for r in res["detail"]["recommendations"] if r["id"] == "i4")
        assert any("兜底" in risk for risk in row["risks"])
    finally:
        conn.close()


def test_recommend_without_any_hotspots_is_not_found(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        res = _run(_env("RecommendHotspots"), Deps(repos=repos))
        assert res["ok"] is False
        assert str(res["error"]).startswith("NOT_FOUND")
        assert "DiscoverHotspots" in str(res["error"])
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 反馈闭环


def test_feedback_changes_next_recommendation(tmp_path: Path) -> None:
    """推荐机制的试金石：用户说「不感兴趣」，它得真的记得住。"""
    conn, repos = _new_db(tmp_path)
    try:
        _add_connection(conn, _write_server(tmp_path))
        _discovered(conn, repos)

        before = _run(_env("RecommendHotspots", {"limit": 4}), Deps(repos=repos))
        first_before = before["detail"]["recommendations"][0]
        target = next(
            r for r in before["detail"]["recommendations"] if r["id"] == first_before["id"]
        )
        score_before = target["score"]

        res = _run(
            _env("RecordHotspotFeedback", {"hotspotId": target["id"], "verdict": "rejected"}),
            Deps(repos=repos),
        )
        assert res["ok"] is True, res
        assert res["detail"]["verdict"] == "rejected"

        after = _run(_env("RecommendHotspots", {"limit": 4}), Deps(repos=repos))
        target_after = next(
            r for r in after["detail"]["recommendations"] if r["id"] == target["id"]
        )
        assert target_after["breakdown"]["feedback"] == 0.0
        assert target_after["score"] < score_before
    finally:
        conn.close()


def test_feedback_is_upsert_not_append(tmp_path: Path) -> None:
    """一条热点只能有一个结论：追加会让学习逻辑无所适从。"""
    conn, repos = _new_db(tmp_path)
    try:
        _add_connection(conn, _write_server(tmp_path))
        _discovered(conn, repos)
        for verdict in ("rejected", "adopted"):
            res = _run(
                _env("RecordHotspotFeedback", {"hotspotId": "i1", "verdict": verdict}),
                Deps(repos=repos),
            )
            assert res["ok"] is True, res
        rows = conn.execute(
            "SELECT * FROM hotspot_feedback WHERE hotspot_id='i1'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["verdict"] == "adopted"
    finally:
        conn.close()


def test_feedback_rejects_bad_verdict(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        res = _run(
            _env("RecordHotspotFeedback", {"hotspotId": "i1", "verdict": "maybe"}),
            Deps(repos=repos),
        )
        assert res["ok"] is False
        assert str(res["error"]).startswith("INVALID_ARGUMENT")
        assert "adopted" in str(res["error"])
    finally:
        conn.close()


def test_feedback_requires_hotspot_id(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        res = _run(_env("RecordHotspotFeedback", {"verdict": "adopted"}), Deps(repos=repos))
        assert res["ok"] is False
        assert "hotspotId" in str(res["error"])
    finally:
        conn.close()


# --------------------------------------------------------------------------
# 迁移


def test_migration_0014_creates_both_tables(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "mig.db"))
    try:
        run_migrations(conn, _MIG_DIR)
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"hotspot_items", "hotspot_feedback"} <= tables
        # 反馈表的主键是（热点, 工作区），不是自增 id
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(hotspot_feedback)")}
        assert {"hotspot_id", "workspace_id", "verdict", "reason"} <= cols
    finally:
        conn.close()


@pytest.mark.parametrize(
    "command",
    ["DiscoverHotspots", "RecommendHotspots", "RecordHotspotFeedback"],
)
def test_hotspot_commands_not_agent_allowed(command: str) -> None:
    """外部 Agent 默认不能触发：Discover 是联网写库，Recommend 会调 AI（计费）。"""
    from worker.runtime.commands.bus import _AGENT_ALLOWED_COMMANDS

    assert command not in _AGENT_ALLOWED_COMMANDS


def test_call_timeout_covers_serial_fetch() -> None:
    """默认 20s 不够：上游 11 源**串行**实测 20–25s。

    第一次真机验收就撞在「MCP Server 在 20s 内无响应」上 —— 这不是上游慢，
    是「一次调用抓全部源」这个形状决定的，故把超时放宽而非拆调用。
    """
    from worker.runtime.agents.mcp_client import DEFAULT_TIMEOUT
    from worker.runtime.hotspot.mcp import DEFAULT_CALL_TIMEOUT

    assert DEFAULT_CALL_TIMEOUT >= 60
    assert DEFAULT_CALL_TIMEOUT > DEFAULT_TIMEOUT


def test_list_sources_also_not_agent_allowed() -> None:
    """与 ListMcpTools 一致：会 spawn 外部进程的命令不开放给外部 Agent。"""
    from worker.runtime.commands.bus import _AGENT_ALLOWED_COMMANDS

    assert "ListHotspotSources" not in _AGENT_ALLOWED_COMMANDS
    assert "ListMcpTools" not in _AGENT_ALLOWED_COMMANDS
