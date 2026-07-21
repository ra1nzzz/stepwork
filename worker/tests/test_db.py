"""Batch 0：DB 层 + 仓储测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import worker.runtime.db.connection as conn_mod
import worker.runtime.db.migrations as mig
from worker.runtime.db.repos import Repos
from worker.runtime.models import ContentProject, ContentVersion, SourceAsset, Workspace

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _fresh() -> tuple[sqlite3.Connection, int]:
    c = conn_mod.in_memory()
    n = mig.run_migrations(c, _MIG_DIR)
    return c, n


def test_migrations_idempotent() -> None:
    c, n = _fresh()
    assert n >= 1
    n2 = mig.run_migrations(c, _MIG_DIR)
    assert n2 == 0
    tables = {
        r["name"]
        for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for t in (
        "workspaces",
        "content_projects",
        "source_assets",
        "jobs",
        "content_versions",
        "schema_migrations",
    ):
        assert t in tables


def test_source_asset_dedup() -> None:
    c, _ = _fresh()
    repos = Repos(c)
    ws = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))

    a1 = SourceAsset(project_id=prj, kind="video", local_uri="file://a.mp4", content_hash="h123")
    a2 = SourceAsset(project_id=prj, kind="video", local_uri="file://b.mp4", content_hash="h123")
    id1 = repos.source_assets.insert_dedup(a1)
    id2 = repos.source_assets.insert_dedup(a2)
    assert id1 == id2

    got = repos.source_assets.get(id1)
    assert got is not None
    assert got.local_uri == "file://a.mp4"


def test_content_version_roundtrip() -> None:
    c, _ = _fresh()
    repos = Repos(c)
    ws = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))

    cv = ContentVersion(
        project_id=prj, content_type="script", content="hello", content_hash="hcv"
    )
    cid = repos.content_versions.insert(cv)
    got = c.execute("SELECT content FROM content_versions WHERE id=?", (cid,)).fetchone()
    assert got["content"] == "hello"
