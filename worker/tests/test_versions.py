"""Tranche 2：ListContentVersions / GetContentVersion 查询测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentProject, ContentVersion, Workspace

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _deps() -> Deps:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    return Deps(repos=Repos(conn), ingest=ingest)


def _env(
    command_type: str,
    payload: dict[str, Any] | None = None,
    workspace_id: str = "ws-v",
) -> dict[str, Any]:
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": workspace_id,
        "payload": payload or {},
        "requestedAt": datetime.now(UTC).isoformat(),
    }


def _seed(deps: Deps) -> tuple[str, list[str]]:
    """建项目 + 3 个版本（transcript / script / script 链）。"""
    repos = deps.repos
    ws_id = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj_id = repos.projects.insert(ContentProject(workspace_id=ws_id, title="p"))
    ids: list[str] = []
    for i, ctype in enumerate(("transcript", "script", "script")):
        ids.append(
            repos.content_versions.insert(
                ContentVersion(
                    project_id=prj_id,
                    parent_version_id=ids[-1] if ids and ctype == "script" else None,
                    content_type=ctype,
                    content=f"内容{i}" + "长" * 300,
                    content_hash=f"h{i}",
                    producer={"kind": "test", "n": i},
                    created_at=f"2026-07-2{i}T00:00:00+00:00",
                )
            )
        )
    return prj_id, ids


async def test_list_content_versions_desc_with_preview() -> None:
    deps = _deps()
    prj_id, ids = _seed(deps)
    res = await dispatch(
        _env("ListContentVersions", {"projectId": prj_id}), deps
    )
    assert res["ok"] is True, res.get("error")
    versions = res["detail"]["versions"]
    assert [v["id"] for v in versions] == list(reversed(ids))
    for v in versions:
        assert len(v["preview"]) <= 200
        assert v["producer"]["kind"] == "test"
        assert "content" not in v  # 列表条目只带 preview，不带全文


async def test_list_content_versions_filter_and_limit() -> None:
    deps = _deps()
    prj_id, _ids = _seed(deps)
    res = await dispatch(
        _env(
            "ListContentVersions",
            {"projectId": prj_id, "contentType": "script", "limit": 1},
        ),
        deps,
    )
    assert res["ok"] is True
    versions = res["detail"]["versions"]
    assert len(versions) == 1
    assert versions[0]["content_type"] == "script"


async def test_list_content_versions_missing_project() -> None:
    deps = _deps()
    res = await dispatch(_env("ListContentVersions", {}), deps)
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]


async def test_get_content_version_full() -> None:
    deps = _deps()
    prj_id, ids = _seed(deps)
    res = await dispatch(
        _env("GetContentVersion", {"versionId": ids[2]}), deps
    )
    assert res["ok"] is True
    version = res["detail"]["version"]
    assert version["id"] == ids[2]
    assert version["project_id"] == prj_id
    assert version["parent_version_id"] == ids[1]
    assert version["content"].startswith("内容2")
    assert len(version["content"]) > 200  # 全文，不截断


async def test_get_content_version_not_found() -> None:
    deps = _deps()
    res = await dispatch(
        _env("GetContentVersion", {"versionId": "cv_nope"}), deps
    )
    assert res["ok"] is False
    assert "NOT_FOUND" in res["error"]
