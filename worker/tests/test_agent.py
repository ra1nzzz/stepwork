"""W8 L.31：Agent 只读列表 handler 测试。

覆盖：
1. ``ListAgentTasks`` 空表返回 ``{tasks: [], note: "..."}``。
2. ``ListAgentArtifacts`` 空表返回 ``{artifacts: [], note: "..."}``。
3. ``ListAgentTasks`` 播种 1 条 agent_task 后返回 1 项。
4. ``GetAgentTask`` 按 id 取单条（兼容 ``taskId`` / ``task_id`` 两种命名）。
5. ``GetAgentTask`` 不存在的 id → ``ok=False``、error 含 ``NOT_FOUND``。

参考 ``worker/tests/test_provenance.py`` 的 ``tmp_path`` + 真实 sqlite3 模式：
独立连接跑完 0001-0004 迁移后播种数据，再经进程内 ``dispatch`` 路由到 handler。
``agent_tasks.target_agent_id`` 有 FK 约束（``foreign_keys=ON``），播种前
先插入一条 ``agent_connections`` 行以满足约束。
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"
_NOTE = "Agent 互操作 V0.2 启用"


def _env(
    command_type: str,
    payload: dict[str, Any] | None = None,
    workspace_id: str = "ws-local",
    actor_type: str = "desktop",
) -> dict[str, Any]:
    """构造一个最小合规信封 dict（对齐 ``command-envelope.schema.json``）。"""
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": actor_type, "id": f"{actor_type}-test"},
        "source": "ui",
        "workspaceId": workspace_id,
        "requestedAt": datetime.now(UTC).isoformat(),
        "payload": payload or {},
    }


def _run(raw: dict[str, Any], deps: Deps) -> dict[str, Any]:
    """在独立事件循环内经 ``dispatch`` 跑一条命令（同步测试内调用）。"""
    return asyncio.run(dispatch(raw, deps))


def _new_db(tmp_path: Path) -> tuple[sqlite3.Connection, Repos]:
    """打开一个跑完 0001-0004 迁移的临时 SQLite 库。"""
    db_path = str(tmp_path / "agent.db")
    conn = connect(db_path)
    run_migrations(conn, _MIG_DIR)
    repos = Repos(conn)
    return conn, repos


def _ensure_agent_connection(
    conn: sqlite3.Connection, conn_id: str = "ac_test"
) -> None:
    """插入一条 ``agent_connections`` 行以满足 ``agent_tasks.target_agent_id`` 的 FK 约束。

    ``agent_connections`` 无 FK 引用其它表，可直接插入；``INSERT OR IGNORE``
    使其在多次调用时幂等。
    """
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO agent_connections "
        "(id, protocol, endpoint_or_command, local_or_remote, trust_level, "
        "auth_ref, status, capabilities, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, NULL, 'inactive', '[]', ?, ?)",
        (conn_id, "stdio", "stepwork-agent", "local", "trusted", now, now),
    )


def _insert_agent_task(
    conn: sqlite3.Connection,
    *,
    tid: str = "at_test",
    target_agent_id: str = "ac_test",
    state: str = "pending",
    task_type: str = "analyze",
) -> None:
    """直接 INSERT 一条 ``agent_tasks`` 行（测试播种）。

    需先确保 ``agent_connections`` 存在对应行（FK 约束，``foreign_keys=ON``）。
    ``session_id`` / ``project_id`` 等可空 FK 列给 NULL；NOT NULL 列全部给值。
    """
    _ensure_agent_connection(conn, target_agent_id)
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO agent_tasks "
        "(id, initiator, target_agent_id, session_id, project_id, task_type, "
        "input_artifact_ids, state, progress, cost, timeout_at, correlation_id, "
        "created_at, updated_at) "
        "VALUES (?, 'user', ?, NULL, NULL, ?, '[]', ?, 0.0, NULL, NULL, NULL, ?, ?)",
        (tid, target_agent_id, task_type, state, now, now),
    )
    conn.commit()


def test_list_agent_tasks_empty(tmp_path: Path) -> None:
    """空表 ``ListAgentTasks`` 返回 ``{tasks: [], note: "..."}``。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        res = _run(_env("ListAgentTasks"), deps)
        assert res["ok"] is True
        assert res["detail"]["tasks"] == []
        assert res["detail"]["note"] == _NOTE
    finally:
        conn.close()


def test_list_agent_artifacts_empty(tmp_path: Path) -> None:
    """空表 ``ListAgentArtifacts`` 返回 ``{artifacts: [], note: "..."}``。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        res = _run(_env("ListAgentArtifacts"), deps)
        assert res["ok"] is True
        assert res["detail"]["artifacts"] == []
        assert res["detail"]["note"] == _NOTE
    finally:
        conn.close()


def test_list_agent_tasks_with_one(tmp_path: Path) -> None:
    """播种 1 条 agent_task 后 ``ListAgentTasks`` 返回 1 项，字段齐全。"""
    conn, repos = _new_db(tmp_path)
    try:
        _insert_agent_task(conn, tid="at_one", state="pending", task_type="analyze")
        deps = Deps(repos=repos)
        res = _run(_env("ListAgentTasks"), deps)
        assert res["ok"] is True
        tasks = res["detail"]["tasks"]
        assert len(tasks) == 1
        t = tasks[0]
        assert t["id"] == "at_one"
        assert t["initiator"] == "user"
        assert t["target_agent_id"] == "ac_test"
        assert t["task_type"] == "analyze"
        assert t["state"] == "pending"
        assert t["progress"] == 0.0
        assert isinstance(t["created_at"], str)
    finally:
        conn.close()


def test_get_agent_task_found(tmp_path: Path) -> None:
    """``GetAgentTask`` 按 id 取单条（兼容 ``taskId`` / ``task_id`` 两种命名）。"""
    conn, repos = _new_db(tmp_path)
    try:
        _insert_agent_task(conn, tid="at_two", state="running", task_type="render")
        deps = Deps(repos=repos)
        # taskId 命名
        res = _run(_env("GetAgentTask", {"taskId": "at_two"}), deps)
        assert res["ok"] is True
        t = res["detail"]["task"]
        assert t["id"] == "at_two"
        assert t["state"] == "running"
        assert t["task_type"] == "render"
        # task_id 命名（兼容）
        res2 = _run(_env("GetAgentTask", {"task_id": "at_two"}), deps)
        assert res2["ok"] is True
        assert res2["detail"]["task"]["id"] == "at_two"
    finally:
        conn.close()


def test_get_agent_task_not_found(tmp_path: Path) -> None:
    """不存在的 id → ``ok=False``、error 含 ``NOT_FOUND``。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        res = _run(_env("GetAgentTask", {"taskId": "at_missing"}), deps)
        assert res["ok"] is False
        assert "NOT_FOUND" in res["error"]
    finally:
        conn.close()
