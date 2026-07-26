"""Tranche 2：发布 MVP（PRD-PUB-001/002）测试。

覆盖：

1. CreatePlatformVariant：锚点解析（videoVersionId > 最新版）/ 平台白名单 /
   无内容版本拒绝 / 主稿不被修改。
2. ListPlatformVariants：按项目列出。
3. ExportBundle（fake ffmpeg）：video.mp4 复制 + cover.jpg + title/body/tags +
   meta.json → detail.bundle_path。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentProject, ContentVersion, Workspace
from worker.runtime.providers.renderer.ffmpeg import FFmpegRenderer
from worker.runtime.render.ffmpeg_runner import FFmpegRunner

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"
PY = sys.executable
FAKE = os.path.join(os.path.dirname(__file__), "fakes", "fake_ffmpeg.py")


def _deps(renderer: Any = None) -> Deps:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    return Deps(repos=Repos(conn), ingest=ingest, renderer=renderer)


def _env(
    command_type: str,
    payload: dict[str, Any] | None = None,
    workspace_id: str = "ws-pub",
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


def _is_dir(path: Path) -> bool:
    """同步 helper：检查目录存在性（避免 async 测试函数内触发 ASYNC240）。"""
    return path.is_dir()


def _seed_project(deps: Deps) -> tuple[str, str]:
    repos = deps.repos
    ws_id = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj_id = repos.projects.insert(ContentProject(workspace_id=ws_id, title="p"))
    cv_id = repos.content_versions.insert(
        ContentVersion(
            project_id=prj_id,
            content_type="script",
            content='{"title": "主稿", "body": "正文"}',
            content_hash="h_master",
        )
    )
    return prj_id, cv_id


async def test_create_variant_anchors_latest_version() -> None:
    deps = _deps()
    prj_id, cv_id = _seed_project(deps)
    res = await dispatch(
        _env(
            "CreatePlatformVariant",
            {
                "projectId": prj_id,
                "platform": "douyin",
                "title": "抖音标题",
                "body": "抖音正文",
                "tags": ["测评", "AI"],
            },
        ),
        deps,
    )
    assert res["ok"] is True, res.get("error")
    variant = res["detail"]["variant"]
    assert variant["id"].startswith("pv_")
    assert variant["project_id"] == prj_id
    assert variant["content_version_id"] == cv_id  # 锚定最新内容版本
    assert variant["video_version_id"] is None
    assert variant["tags"] == ["测评", "AI"]
    # 主稿绝不被修改
    row = deps.repos.conn.execute(
        "SELECT content FROM content_versions WHERE id=?", (cv_id,)
    ).fetchone()
    assert row["content"] == '{"title": "主稿", "body": "正文"}'


async def test_create_variant_invalid_platform() -> None:
    deps = _deps()
    prj_id, _ = _seed_project(deps)
    res = await dispatch(
        _env(
            "CreatePlatformVariant",
            {"projectId": prj_id, "platform": "weibo", "title": "t", "tags": []},
        ),
        deps,
    )
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]


async def test_create_variant_requires_content_version() -> None:
    deps = _deps()
    repos = deps.repos
    ws_id = repos.workspaces.insert(Workspace(name="ws0", root_path="/t"))
    prj_id = repos.projects.insert(ContentProject(workspace_id=ws_id, title="空"))
    res = await dispatch(
        _env(
            "CreatePlatformVariant",
            {"projectId": prj_id, "platform": "generic", "title": "t", "tags": []},
        ),
        deps,
    )
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]


async def test_create_variant_bad_video_version() -> None:
    deps = _deps()
    prj_id, _ = _seed_project(deps)
    res = await dispatch(
        _env(
            "CreatePlatformVariant",
            {
                "projectId": prj_id,
                "platform": "generic",
                "title": "t",
                "tags": [],
                "videoVersionId": "cv_nope",
            },
        ),
        deps,
    )
    assert res["ok"] is False
    assert "NOT_FOUND" in res["error"]


async def test_list_platform_variants() -> None:
    deps = _deps()
    prj_id, _ = _seed_project(deps)
    for title in ("v1", "v2"):
        await dispatch(
            _env(
                "CreatePlatformVariant",
                {
                    "projectId": prj_id,
                    "platform": "generic",
                    "title": title,
                    "tags": [],
                },
            ),
            deps,
        )
    res = await dispatch(
        _env("ListPlatformVariants", {"projectId": prj_id}), deps
    )
    assert res["ok"] is True
    assert {v["title"] for v in res["detail"]["variants"]} == {"v1", "v2"}


async def test_export_bundle_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ExportBundle：video 复制 + cover（fake ffmpeg）+ 三 txt + meta.json。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    renderer = FFmpegRenderer(FFmpegRunner(bin_path=PY), ffmpeg_bin=FAKE)
    deps = _deps(renderer)
    prj_id, _script_cv = _seed_project(deps)

    # 渲染产物：video_draft 版本 + 真实 mp4 文件
    video_file = tmp_path / "draft.mp4"
    video_file.write_bytes(b"mp4-bytes")
    draft_cv = deps.repos.content_versions.insert(
        ContentVersion(
            project_id=prj_id,
            content_type="video_draft",
            content=json.dumps({"video_uri": f"file://{video_file}"}),
            content_hash="h_draft",
        )
    )
    created = await dispatch(
        _env(
            "CreatePlatformVariant",
            {
                "projectId": prj_id,
                "platform": "douyin",
                "title": "导出标题",
                "body": "导出正文",
                "tags": ["tag1", "tag2"],
                "videoVersionId": draft_cv,
            },
        ),
        deps,
    )
    variant_id = created["detail"]["variant"]["id"]

    res = await dispatch(_env("ExportBundle", {"variantId": variant_id}), deps)
    assert res["ok"] is True, res.get("error")
    bundle = Path(res["detail"]["bundle_path"])
    assert _is_dir(bundle)
    assert str(bundle).startswith(str(tmp_path / "exports"))

    assert (bundle / "video.mp4").read_bytes() == b"mp4-bytes"
    assert (bundle / "cover.jpg").exists()  # fake ffmpeg touch 输出
    assert (bundle / "title.txt").read_text(encoding="utf-8") == "导出标题"
    assert (bundle / "body.txt").read_text(encoding="utf-8") == "导出正文"
    assert (bundle / "tags.txt").read_text(encoding="utf-8") == "tag1\ntag2"
    meta = json.loads((bundle / "meta.json").read_text(encoding="utf-8"))
    assert meta["variant"]["id"] == variant_id
    assert meta["files"]["video"] == "video.mp4"
    assert meta["files"]["cover"] == "cover.jpg"


async def test_export_bundle_without_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无渲染产物的 variant：bundle 仍产出文本 + meta（video/cover 为 null）。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps()
    prj_id, _ = _seed_project(deps)
    created = await dispatch(
        _env(
            "CreatePlatformVariant",
            {
                "projectId": prj_id,
                "platform": "generic",
                "title": "纯文本",
                "body": "b",
                "tags": [],
            },
        ),
        deps,
    )
    variant_id = created["detail"]["variant"]["id"]
    res = await dispatch(_env("ExportBundle", {"variantId": variant_id}), deps)
    assert res["ok"] is True, res.get("error")
    bundle = Path(res["detail"]["bundle_path"])
    assert not (bundle / "video.mp4").exists()
    meta = json.loads((bundle / "meta.json").read_text(encoding="utf-8"))
    assert meta["files"]["video"] is None
    assert meta["files"]["cover"] is None


async def test_export_bundle_not_found() -> None:
    deps = _deps()
    res = await dispatch(_env("ExportBundle", {"variantId": "pv_nope"}), deps)
    assert res["ok"] is False
    assert "NOT_FOUND" in res["error"]
