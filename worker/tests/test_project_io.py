"""W9 L.39：项目导出/导入 handler 测试。

覆盖：

1. ``test_export_project_not_found``：导出不存在项目 → NOT_FOUND
2. ``test_export_project_missing_project_id``：payload 无 projectId → INVALID_ARGUMENT
3. ``test_export_project_bundle_contents``：完整导出后断言 zip 结构与 manifest 计数
4. ``test_import_project_roundtrip``：导出后导入到新 workspace，新 id 与原 id 不同
5. ``test_import_project_remap_id``：remapId=True 时 id_map 非空、title 含 ``(imported``
6. ``test_import_project_rejects_path_traversal``：恶意 zip（含 ``../``）→ INVALID_ARGUMENT
7. ``test_import_project_missing_bundle_path``：payload 无 bundlePath → INVALID_ARGUMENT

参考 ``worker/tests/test_commands.py`` 的 ``_deps`` / ``_envelope`` 风格，
``STEPWORK_HOME`` 指向 ``tmp_path`` 避免污染真实家目录。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _deps() -> Deps:
    """构造内存库 + 迁移 + Deps（ingest 注入以便 ImportSource 计算 hash）。"""
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    return Deps(repos=Repos(c), ingest=ingest, asr=None, ai=None)


def _envelope(
    command_type: str,
    payload: dict[str, Any],
    project_id: str | None = None,
    workspace_id: str = "ws-1",
) -> dict[str, Any]:
    """构造最小合规命令信封 dict（对齐 command-envelope.schema.json）。"""
    return {
        "commandId": "cmd-1",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": workspace_id,
        "projectId": project_id,
        "payload": payload,
        "requestedAt": "2026-07-23T00:00:00+00:00",
    }


async def test_export_project_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """导出不存在项目 → DispatchError NOT_FOUND。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps()
    deps.repos.workspaces.ensure("ws-1")
    res = await dispatch(
        _envelope(
            "ExportProject",
            {"projectId": "prj_nonexistent"},
            project_id="prj_nonexistent",
        ),
        deps,
    )
    assert res["ok"] is False
    assert "NOT_FOUND" in res["error"]


async def test_export_project_missing_project_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """payload 无 projectId → DispatchError INVALID_ARGUMENT。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps()
    deps.repos.workspaces.ensure("ws-1")
    res = await dispatch(_envelope("ExportProject", {}), deps)
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]


async def test_export_project_bundle_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """完整导出：ImportSource 建项目+asset → SaveScript 建版本 → ExportProject。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps()

    # ImportSource 建项目 + asset
    imp_res = await dispatch(
        _envelope(
            "ImportSource",
            {
                "local_uri": "file://a.mp4",
                "content_hash": "h_bundle_test",
                "kind": "video",
            },
        ),
        deps,
    )
    assert imp_res["ok"] is True

    project_id = deps.repos.projects.get_or_create_default("ws-1").id

    # SaveScript 建版本
    save_res = await dispatch(
        _envelope("SaveScript", {"content": "hello world"}, project_id=project_id),
        deps,
    )
    assert save_res["ok"] is True

    # ExportProject
    res = await dispatch(
        _envelope("ExportProject", {"projectId": project_id}, project_id=project_id),
        deps,
    )
    assert res["ok"] is True
    detail = res["detail"]
    assert detail["project_id"] == project_id
    assert detail["versions_count"] >= 1
    assert detail["assets_count"] >= 1
    assert detail["size_bytes"] > 0

    bundle_path = Path(detail["bundle_path"])

    with zipfile.ZipFile(bundle_path, "r") as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "project.json" in names
        assert "versions.json" in names
        assert "assets.json" in names
        assert "jobs.json" in names

        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["schema_version"] == "1"
        assert manifest["project_id"] == project_id
        assert manifest["versions_count"] >= 1
        assert manifest["assets_count"] >= 1

        project = json.loads(zf.read("project.json"))
        assert "title" in project
        assert project["id"] == project_id


async def test_import_project_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """导出后导入到新 workspace：新 project_id != 原 id，计数 >= 1。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps()

    # 建项目 + asset + 版本
    await dispatch(
        _envelope(
            "ImportSource",
            {
                "local_uri": "file://a.mp4",
                "content_hash": "h_roundtrip",
                "kind": "video",
            },
        ),
        deps,
    )
    project_id = deps.repos.projects.get_or_create_default("ws-1").id
    await dispatch(
        _envelope("SaveScript", {"content": "roundtrip content"}, project_id=project_id),
        deps,
    )

    # 导出
    exp_res = await dispatch(
        _envelope("ExportProject", {"projectId": project_id}, project_id=project_id),
        deps,
    )
    assert exp_res["ok"] is True
    bundle_path = exp_res["detail"]["bundle_path"]

    # 导入到新 workspace（ws-2）
    imp_res = await dispatch(
        _envelope("ImportProject", {"bundlePath": bundle_path}, workspace_id="ws-2"),
        deps,
    )
    assert imp_res["ok"] is True
    detail = imp_res["detail"]
    new_pid = detail["project_id"]
    assert new_pid != project_id
    assert detail["imported_versions"] >= 1
    assert detail["imported_assets"] >= 1


async def test_import_project_remap_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """remapId=True 时 id_map 非空，新 project title 含 ``(imported``。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps()

    await dispatch(
        _envelope(
            "ImportSource",
            {
                "local_uri": "file://a.mp4",
                "content_hash": "h_remap",
                "kind": "video",
            },
        ),
        deps,
    )
    project_id = deps.repos.projects.get_or_create_default("ws-1").id
    await dispatch(
        _envelope("SaveScript", {"content": "remap content"}, project_id=project_id),
        deps,
    )

    exp_res = await dispatch(
        _envelope("ExportProject", {"projectId": project_id}, project_id=project_id),
        deps,
    )
    bundle_path = exp_res["detail"]["bundle_path"]

    imp_res = await dispatch(
        _envelope(
            "ImportProject",
            {"bundlePath": bundle_path, "remapId": True},
            workspace_id="ws-2",
        ),
        deps,
    )
    assert imp_res["ok"] is True
    detail = imp_res["detail"]
    assert len(detail["id_map"]) > 0

    new_pid = detail["project_id"]
    row = deps.repos.conn.execute(
        "SELECT title FROM content_projects WHERE id=?", (new_pid,)
    ).fetchone()
    assert row is not None
    assert "(imported" in str(row["title"])


async def test_import_project_rejects_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """恶意 zip（含 ``../evil.txt``）→ DispatchError INVALID_ARGUMENT。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps()
    deps.repos.workspaces.ensure("ws-1")

    evil_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("../evil.txt", "x")

    res = await dispatch(
        _envelope("ImportProject", {"bundlePath": str(evil_zip)}),
        deps,
    )
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]


async def test_import_project_missing_bundle_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """payload 无 bundlePath → DispatchError INVALID_ARGUMENT。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps()
    deps.repos.workspaces.ensure("ws-1")

    res = await dispatch(_envelope("ImportProject", {}), deps)
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]
