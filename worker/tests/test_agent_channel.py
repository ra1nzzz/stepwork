"""出站 Agent 通道公共层的不变量（agents/channel.py）。

三个协议（MCP / A2A / ACP）此前各抄一遍留痕与连接校验逻辑。抽公共层的风险
是「抽得不对，三个协议里有一个行为悄悄变了」——所以这里锁住的是**三者必须
一致的那些语义**，而不是实现细节。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from worker.runtime.agents.channel import (
    REVIEW_STATE,
    TRUST_LEVEL,
    insert_connection,
    load_connection,
    record_call,
    require,
    sync_capabilities,
)
from worker.runtime.commands.bus import DispatchError
from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, ContentProject, Workspace

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _deps(tmp_path: Path) -> tuple[sqlite3.Connection, Deps]:
    conn = connect(str(tmp_path / "ch.db"))
    run_migrations(conn, _MIG_DIR)
    return conn, Deps(repos=Repos(conn))


def _env(project_id: str | None = None) -> CommandEnvelope:
    return CommandEnvelope.model_validate(
        {
            "commandId": "cid-x",
            "commandType": "CallMcpTool",
            "schemaVersion": "1",
            "actor": {"type": "desktop", "id": "ui"},
            "source": "ui",
            "workspaceId": "ws-local",
            "projectId": project_id,
            "requestedAt": datetime.now(UTC).isoformat(),
            "payload": {},
        }
    )


def _seed_conn(deps: Deps, protocol: str = "mcp-client") -> str:
    insert_connection(
        deps,
        conn_id=f"c_{protocol}",
        protocol=protocol,
        endpoint="cmd",
        local_or_remote="local",
        capabilities=[],
    )
    deps.repos.conn.commit()
    return f"c_{protocol}"


# ---------------------------------------------------------------------------
# 连接校验：三个协议必须一致
# ---------------------------------------------------------------------------


def test_wrong_protocol_is_rejected(tmp_path: Path) -> None:
    """协议串用必须挡住。

    三种连接的 endpoint_or_command 语义完全不同（stdio 命令行 / HTTP 地址 /
    本地 Agent 命令行），串用会拿命令行当 URL 去连，报的错完全看不懂。
    """
    conn, deps = _deps(tmp_path)
    try:
        cid = _seed_conn(deps, "mcp-client")
        with pytest.raises(DispatchError) as e:
            load_connection(deps, cid, protocol="a2a-client", label="出站 A2A 连接")
        assert "出站 A2A 连接" in str(e.value)
    finally:
        conn.close()


def test_disabled_connection_is_rejected(tmp_path: Path) -> None:
    """PRD-AGT-007：停用的通道不可调用（三协议同一条规则）。"""
    conn, deps = _deps(tmp_path)
    try:
        cid = _seed_conn(deps)
        conn.execute("UPDATE agent_connections SET status='inactive' WHERE id=?", (cid,))
        conn.commit()
        with pytest.raises(DispatchError) as e:
            load_connection(deps, cid, protocol="mcp-client", label="出站 MCP 连接")
        assert e.value.code == "CONNECTION_DISABLED"
    finally:
        conn.close()


def test_missing_connection_is_not_found(tmp_path: Path) -> None:
    conn, deps = _deps(tmp_path)
    try:
        with pytest.raises(DispatchError) as e:
            load_connection(deps, "nope", protocol="mcp-client", label="x")
        assert e.value.code == "NOT_FOUND"
    finally:
        conn.close()


def test_new_connections_start_untrusted(tmp_path: Path) -> None:
    """出站连接一律 external-unverified —— 不能因为是用户自己加的就默认可信。"""
    conn, deps = _deps(tmp_path)
    try:
        cid = _seed_conn(deps)
        row = conn.execute(
            "SELECT trust_level, status FROM agent_connections WHERE id=?", (cid,)
        ).fetchone()
        assert row["trust_level"] == TRUST_LEVEL
        assert row["status"] == "active"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 留痕：PRD-AGT-003
# ---------------------------------------------------------------------------


def test_successful_call_records_task_and_artifact(tmp_path: Path) -> None:
    conn, deps = _deps(tmp_path)
    try:
        cid = _seed_conn(deps)
        ws_id = deps.repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
        pid = deps.repos.projects.insert(ContentProject(workspace_id=ws_id, title="p"))
        task_id = record_call(
            deps, _env(pid), conn_id=cid, task_type="mcp:search", text="结果", ok=True
        )
        task = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
        assert task["state"] == "succeeded"
        assert task["task_type"] == "mcp:search"
        assert task["target_agent_id"] == cid

        art = conn.execute(
            "SELECT * FROM agent_artifacts WHERE agent_task_id=?", (task_id,)
        ).fetchone()
        assert art["trust_level"] == TRUST_LEVEL
        assert art["review_state"] == REVIEW_STATE
        # 三协议统一的 artifact JSON 形状（此前 MCP 用 tool、A2A 用 skill、
        # ACP 什么都不带，复核端要按协议分支解析）
        assert json.loads(art["content_uri_or_json"]) == {
            "text": "结果",
            "task_type": "mcp:search",
        }
    finally:
        conn.close()


def test_failed_call_still_records_task(tmp_path: Path) -> None:
    """失败也要留痕：连接页要能看出「这个通道一直在报错」。"""
    conn, deps = _deps(tmp_path)
    try:
        cid = _seed_conn(deps)
        task_id = record_call(
            deps, _env(), conn_id=cid, task_type="a2a:x", text="", ok=False
        )
        task = conn.execute("SELECT state FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
        assert task["state"] == "failed"
        # 失败不产出 artifact（没有可复核的内容）
        n = conn.execute("SELECT COUNT(*) n FROM agent_artifacts").fetchone()["n"]
        assert n == 0
    finally:
        conn.close()


def test_no_project_means_no_artifact(tmp_path: Path) -> None:
    """agent_artifacts.project_id 是 NOT NULL —— 无项目上下文时只记 task。"""
    conn, deps = _deps(tmp_path)
    try:
        cid = _seed_conn(deps)
        record_call(deps, _env(None), conn_id=cid, task_type="acp:prompt", text="x", ok=True)
        assert conn.execute("SELECT COUNT(*) n FROM agent_tasks").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) n FROM agent_artifacts").fetchone()["n"] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 能力目录
# ---------------------------------------------------------------------------


def test_sync_capabilities_replaces_rather_than_merges(tmp_path: Path) -> None:
    """先清后插：对方删掉的工具我们不该还留着。

    留着会让用户调用一个已经不存在的能力，拿到一个莫名其妙的远端错误。
    """
    conn, deps = _deps(tmp_path)
    try:
        cid = _seed_conn(deps)
        sync_capabilities(deps, cid, [{"name": "a"}, {"name": "b"}])
        conn.commit()
        assert conn.execute("SELECT COUNT(*) n FROM agent_capabilities").fetchone()["n"] == 2

        sync_capabilities(deps, cid, [{"name": "a"}])
        conn.commit()
        rows = conn.execute("SELECT capability_key FROM agent_capabilities").fetchall()
        assert [r["capability_key"] for r in rows] == ["a"]
    finally:
        conn.close()


def test_sync_capabilities_supports_alternate_key(tmp_path: Path) -> None:
    """A2A 的 skill 用 id 而非 name 作键。"""
    conn, deps = _deps(tmp_path)
    try:
        cid = _seed_conn(deps)
        sync_capabilities(deps, cid, [{"id": "script-drafting"}], key="id")
        conn.commit()
        row = conn.execute("SELECT capability_key FROM agent_capabilities").fetchone()
        assert row["capability_key"] == "script-drafting"
    finally:
        conn.close()


def test_sync_capabilities_skips_nameless_entries(tmp_path: Path) -> None:
    """对端给的目录是不可信输入，缺名字的条目跳过而不是写个空键。"""
    conn, deps = _deps(tmp_path)
    try:
        cid = _seed_conn(deps)
        sync_capabilities(deps, cid, [{"name": ""}, {"description": "无名"}, {"name": "ok"}])
        conn.commit()
        rows = conn.execute("SELECT capability_key FROM agent_capabilities").fetchall()
        assert [r["capability_key"] for r in rows] == ["ok"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 参数别名
# ---------------------------------------------------------------------------


def test_require_accepts_aliases() -> None:
    assert require({"connection_id": "x"}, "connectionId", "connection_id") == "x"
    assert require({"connectionId": "y"}, "connectionId", "connection_id") == "y"


def test_require_reports_the_documented_spelling() -> None:
    """报错要用对外文档里的那个拼法，而不是内部别名。"""
    with pytest.raises(DispatchError) as e:
        require({}, "connectionId", "connection_id")
    assert "connectionId required" in str(e.value)


def test_require_treats_empty_string_as_missing() -> None:
    """空串当缺失：前端传空输入框比不传更常见。"""
    with pytest.raises(DispatchError):
        require({"connectionId": ""}, "connectionId")
