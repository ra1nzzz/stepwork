"""W8 L.30：Provenance 只读聚合 handler 测试。

覆盖：
1. ``GetProvenance`` 查不存在的 subject → ``ok=False``、error 含 ``NOT_FOUND``。
2. ``provenance_records`` 有记录 → 返回该记录（JSON 列正确解析）。
3. 无 ``provenance_records`` 但 ``content_versions.producer`` 有值 → 回退聚合，
   ``ai_label_state="ai-generated"``、``model_calls`` 含一条 AI 调用。
4. ``producer`` 是非法 JSON → 不崩溃，``ai_label_state="unknown"``。

参考 ``worker/tests/test_plugins.py`` 的 ``tmp_path`` + 真实 sqlite3 模式：
独立连接跑完 0001-0004 迁移后播种数据，再经进程内 ``dispatch`` 路由到 handler。
"""

from __future__ import annotations

import asyncio
import json
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
    db_path = str(tmp_path / "provenance.db")
    conn = connect(db_path)
    run_migrations(conn, _MIG_DIR)
    repos = Repos(conn)
    return conn, repos


def _insert_provenance_record(
    conn: sqlite3.Connection,
    *,
    subject_type: str,
    subject_id: str,
    source_ids: list[str] | None = None,
    model_calls: list[dict[str, Any]] | None = None,
    ai_label_state: str = "ai-generated",
) -> None:
    """直接 INSERT 一条 ``provenance_records`` 行（测试播种）。

    ``provenance_records`` 无 FK 约束，可直接插入。
    """
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO provenance_records "
        "(id, subject_type, subject_id, source_ids, model_calls, "
        "agent_tasks, plugin_executions, user_edits, ai_label_state, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"prov_{subject_id}",
            subject_type,
            subject_id,
            json.dumps(source_ids or []),
            json.dumps(model_calls or []),
            "[]",
            "[]",
            "[]",
            ai_label_state,
            now,
            now,
        ),
    )
    conn.commit()


def _insert_content_version(
    conn: sqlite3.Connection,
    *,
    cv_id: str,
    producer: Any,
    project_id: str = "prj_test",
    content_type: str = "analysis",
) -> None:
    """直接 INSERT 一条 ``content_versions`` 行（测试播种）。

    ``producer`` 可传 dict（自动序列化）或原始字符串（用于非法 JSON 用例）。
    需先确保 workspace + project 存在（FK 约束）。
    """
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO workspaces "
        "(id, name, root_path, settings, created_at, archived_at) "
        "VALUES (?, ?, ?, '{}', ?, NULL)",
        ("ws-local", "ws-local", "STEPWORK_HOME/workspaces/ws-local", now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO content_projects "
        "(id, workspace_id, title, status, brand_profile_id, "
        "current_content_version_id, created_at, updated_at) "
        "VALUES (?, ?, 'default', 'active', NULL, NULL, ?, ?)",
        (project_id, "ws-local", now, now),
    )
    producer_json = (
        producer if isinstance(producer, str) else json.dumps(producer, ensure_ascii=False)
    )
    conn.execute(
        "INSERT INTO content_versions "
        "(id, project_id, parent_version_id, content_type, content, "
        "content_hash, producer, created_at) "
        "VALUES (?, ?, NULL, ?, '', ?, ?, ?)",
        (cv_id, project_id, content_type, "hash_" + cv_id, producer_json, now),
    )
    conn.commit()


def test_get_provenance_not_found(tmp_path: Path) -> None:
    """查不存在的 subject → ok=False、error 含 NOT_FOUND。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        res = _run(
            _env(
                "GetProvenance",
                {"subjectType": "content_version", "subjectId": "cv_missing"},
            ),
            deps,
        )
        assert res["ok"] is False
        assert "NOT_FOUND" in res["error"]
    finally:
        conn.close()


def test_get_provenance_from_records_table(tmp_path: Path) -> None:
    """provenance_records 有记录 → 返回该记录（JSON 列正确解析）。"""
    conn, repos = _new_db(tmp_path)
    try:
        _insert_provenance_record(
            conn,
            subject_type="content_version",
            subject_id="cv_1",
            source_ids=["asset_a", "asset_b"],
            model_calls=[
                {"kind": "ai-analysis", "provider": "openai", "model": "gpt-4o"}
            ],
            ai_label_state="ai-generated",
        )
        deps = Deps(repos=repos)
        res = _run(
            _env(
                "GetProvenance",
                {"subjectType": "content_version", "subjectId": "cv_1"},
            ),
            deps,
        )
        assert res["ok"] is True
        prov = res["detail"]["provenance"]
        assert prov["subject_type"] == "content_version"
        assert prov["subject_id"] == "cv_1"
        assert prov["source_ids"] == ["asset_a", "asset_b"]
        assert prov["model_calls"] == [
            {"kind": "ai-analysis", "provider": "openai", "model": "gpt-4o"}
        ]
        assert prov["agent_tasks"] == []
        assert prov["plugin_executions"] == []
        assert prov["user_edits"] == []
        assert prov["ai_label_state"] == "ai-generated"
        assert isinstance(prov["created_at"], str)
        assert isinstance(prov["updated_at"], str)
    finally:
        conn.close()


def test_get_provenance_fallback_to_producer(tmp_path: Path) -> None:
    """无 provenance_records 但 content_versions.producer 有值 → 回退聚合返 ai-generated。"""
    conn, repos = _new_db(tmp_path)
    try:
        _insert_content_version(
            conn,
            cv_id="cv_2",
            producer={
                "kind": "ai-analysis",
                "provider": "openai",
                "model": "gpt-4o",
                "schema_version": "analysis.schema.json",
            },
        )
        deps = Deps(repos=repos)
        res = _run(
            _env(
                "GetProvenance",
                {"subjectType": "content_version", "subjectId": "cv_2"},
            ),
            deps,
        )
        assert res["ok"] is True
        prov = res["detail"]["provenance"]
        assert prov["ai_label_state"] == "ai-generated"
        assert prov["model_calls"] == [
            {"kind": "ai-analysis", "provider": "openai", "model": "gpt-4o"}
        ]
        assert prov["source_ids"] == []
        assert prov["agent_tasks"] == []
        assert prov["plugin_executions"] == []
        assert prov["user_edits"] == []
    finally:
        conn.close()


def test_get_provenance_fallback_invalid_json(tmp_path: Path) -> None:
    """producer 是非法 JSON → 不崩溃，ai_label_state="unknown"。"""
    conn, repos = _new_db(tmp_path)
    try:
        _insert_content_version(
            conn,
            cv_id="cv_3",
            producer="{not valid json",
        )
        deps = Deps(repos=repos)
        res = _run(
            _env(
                "GetProvenance",
                {"subjectType": "content_version", "subjectId": "cv_3"},
            ),
            deps,
        )
        assert res["ok"] is True
        prov = res["detail"]["provenance"]
        assert prov["ai_label_state"] == "unknown"
        assert prov["model_calls"] == []
        assert prov["source_ids"] == []
    finally:
        conn.close()
