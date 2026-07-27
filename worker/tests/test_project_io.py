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
import os
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
from worker.runtime.models import ContentProject, SourceAsset


def _read_file_bytes(path: str) -> bytes | None:
    """同步读文件（放在 async 测试外，避免 ruff ASYNC230/240）。"""
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return f.read()


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


# ----- PRD-WS-004：bundle 必须带媒体本体，才能「在另一安装实例恢复」 -----


async def test_export_bundle_includes_media_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """导出包内应含 assets/ 下的媒体本体（此前只有 JSON 行数据）。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps()
    deps.repos.workspaces.ensure("ws-media")
    prj = deps.repos.projects.insert(
        ContentProject(workspace_id="ws-media", title="带素材项目")
    )
    media = tmp_path / "source.mp4"
    media.write_bytes(b"FAKE-MEDIA-BYTES")
    deps.repos.source_assets.insert_dedup(
        SourceAsset(
            project_id=prj,
            kind="video",
            local_uri=str(media),
            content_hash="h-media",
        )
    )

    res = await dispatch(
        _envelope("ExportProject", {"projectId": prj}, project_id=prj), deps
    )
    assert res["ok"] is True, res.get("error")

    with zipfile.ZipFile(res["detail"]["bundle_path"]) as zf:
        names = zf.namelist()
        media_members = [n for n in names if n.startswith("assets/")]
        assert media_members, f"bundle 未包含媒体本体：{names}"
        assert zf.read(media_members[0]) == b"FAKE-MEDIA-BYTES"


async def test_import_restores_media_and_rewrites_local_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """跨机导入：媒体落到本机 assets 目录，local_uri 改写为可用路径。

    模拟「另一台机器」：导出后把源文件删掉，再用新的 STEPWORK_HOME 导入。
    修复前 local_uri 仍指向导出机路径 → 素材失效。
    """
    export_home = tmp_path / "machine-a"
    export_home.mkdir()
    monkeypatch.setenv("STEPWORK_HOME", str(export_home))

    deps = _deps()
    deps.repos.workspaces.ensure("ws-a")
    prj = deps.repos.projects.insert(
        ContentProject(workspace_id="ws-a", title="跨机项目")
    )
    media = tmp_path / "original.mp4"
    media.write_bytes(b"CROSS-MACHINE-MEDIA")
    deps.repos.source_assets.insert_dedup(
        SourceAsset(
            project_id=prj, kind="video", local_uri=str(media), content_hash="h-x"
        )
    )
    exported = await dispatch(
        _envelope("ExportProject", {"projectId": prj}, project_id=prj), deps
    )
    assert exported["ok"] is True
    bundle = exported["detail"]["bundle_path"]

    # 「另一台机器」：源文件不存在、家目录不同、空库
    media.unlink()
    import_home = tmp_path / "machine-b"
    import_home.mkdir()
    monkeypatch.setenv("STEPWORK_HOME", str(import_home))
    deps2 = _deps()
    deps2.repos.workspaces.ensure("ws-b")

    imported = await dispatch(
        _envelope("ImportProject", {"bundlePath": bundle}, workspace_id="ws-b"), deps2
    )
    assert imported["ok"] is True, imported.get("error")

    row = deps2.repos.conn.execute(
        "SELECT local_uri FROM source_assets"
    ).fetchone()
    assert row is not None
    restored = str(row["local_uri"])
    # 关键：指向本机新路径且文件真实存在、内容一致
    assert _read_file_bytes(restored) == b"CROSS-MACHINE-MEDIA", (
        f"素材未落盘或内容不符：{restored}"
    )
    assert str(import_home) in restored


async def test_import_without_media_keeps_original_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧格式 bundle（无 assets/ 成员）导入仍可用，local_uri 沿用原值。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps()
    deps.repos.workspaces.ensure("ws-old")
    prj = deps.repos.projects.insert(
        ContentProject(workspace_id="ws-old", title="无素材文件")
    )
    # local_uri 指向不存在的文件 → 导出时不会打包媒体
    deps.repos.source_assets.insert_dedup(
        SourceAsset(
            project_id=prj,
            kind="video",
            local_uri=str(tmp_path / "missing.mp4"),
            content_hash="h-missing",
        )
    )
    exported = await dispatch(
        _envelope("ExportProject", {"projectId": prj}, project_id=prj), deps
    )
    assert exported["ok"] is True
    with zipfile.ZipFile(exported["detail"]["bundle_path"]) as zf:
        assert not [n for n in zf.namelist() if n.startswith("assets/")]

    imported = await dispatch(
        _envelope(
            "ImportProject",
            {"bundlePath": exported["detail"]["bundle_path"]},
            workspace_id="ws-old",
        ),
        deps,
    )
    assert imported["ok"] is True, imported.get("error")
