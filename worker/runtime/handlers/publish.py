"""发布 MVP 命令处理（Tranche 2，PRD-PUB-001/002）。

三个命令：

- ``CreatePlatformVariant``：payload {projectId, platform("douyin"|"generic"),
  title, body, tags: string[], videoVersionId?} → INSERT ``platform_variants``
  （0003 表 + 0005 补 project_id / video_version_id 列）→ detail.variant。
  主稿（``content_versions``）**绝不修改**——variant 是独立行，仅引用版本。
- ``ListPlatformVariants``：payload {projectId} → detail.variants。
- ``ExportBundle``：payload {variantId} → 在
  ``$STEPWORK_HOME/exports/bundle-<variantId>-<ts>/`` 下产出
  video.mp4（复制自渲染 artifact，若有）/ cover.jpg（ffmpeg 取 1s 帧，
  走 ffmpeg_runner 既有封装；不可用时跳过）/ title.txt / body.txt /
  tags.txt / meta.json → detail.bundle_path。

锚点解析：``platform_variants.content_version_id``（0003 起 NOT NULL）取
``videoVersionId`` > 项目 ``current_content_version_id`` > 项目最新
content_version；项目尚无任何内容版本时拒绝创建（发布必须有主稿锚点）。
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime.cleanup import resolve_stepwork_home
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult
from worker.runtime.render.ffmpeg_runner import FFmpegRunner

# 平台白名单（PRD-PUB-001 MVP）
_PLATFORMS: tuple[str, ...] = ("douyin", "generic")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_tags(raw: Any) -> list[str]:
    try:
        parsed = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [str(t) for t in parsed] if isinstance(parsed, list) else []


def _row_to_variant(row: Any) -> dict[str, Any]:
    """把 ``platform_variants`` 行转为可序列化 dict。"""
    return {
        "id": str(row["id"]),
        "project_id": (
            str(row["project_id"]) if row["project_id"] is not None else None
        ),
        "content_version_id": str(row["content_version_id"]),
        "video_version_id": (
            str(row["video_version_id"])
            if row["video_version_id"] is not None
            else None
        ),
        "platform": str(row["platform"]),
        "title": str(row["title"] or ""),
        "body": str(row["body"] or ""),
        "tags": _load_tags(row["tags"]),
        "validation_status": str(row["validation_status"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _resolve_anchor_version(
    repos: Any, project_id: str, video_version_id: str | None
) -> str:
    """解析 variant 的 content_version 锚点（videoVersionId > 当前版 > 最新版）。"""
    if video_version_id:
        cv = repos.content_versions.get(video_version_id)
        if cv is None or cv.project_id != project_id:
            raise DispatchError(
                "NOT_FOUND", f"version {video_version_id!r} not found in project"
            )
        return str(video_version_id)
    prj = repos.conn.execute(
        "SELECT current_content_version_id FROM content_projects WHERE id=?",
        (project_id,),
    ).fetchone()
    if prj is not None and prj["current_content_version_id"] is not None:
        return str(prj["current_content_version_id"])
    latest = repos.conn.execute(
        "SELECT id FROM content_versions WHERE project_id=? "
        "ORDER BY created_at DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if latest is None:
        raise DispatchError(
            "INVALID_ARGUMENT",
            "project has no content versions; create content before a variant",
        )
    return str(latest["id"])


def _resolve_video_path(repos: Any, video_version_id: str | None) -> str | None:
    """从 video_draft 版本解析渲染产物 mp4 的本地路径（无/缺文件返回 None）。"""
    if not video_version_id:
        return None
    cv = repos.content_versions.get(video_version_id)
    if cv is None or cv.content_type != "video_draft":
        return None
    try:
        meta = json.loads(cv.content)
    except (TypeError, ValueError):
        return None
    video_uri = str(meta.get("video_uri") or "")
    if not video_uri:
        return None
    path = Path(video_uri[7:] if video_uri.startswith("file://") else video_uri)
    return str(path) if path.is_file() else None


def _extract_cover(deps: Deps, video_path: str, cover_path: Path) -> bool:
    """ffmpeg 取 1s 帧生成封面（走 ffmpeg_runner 既有封装）；失败即跳过。"""
    renderer = deps.renderer
    runner = getattr(renderer, "runner", None) or FFmpegRunner()
    args = [
        "-y", "-ss", "1", "-i", video_path,
        "-frames:v", "1", str(cover_path),
    ]
    # fake ffmpeg 测试模式（与 FFmpegRenderer 一致）：ffmpeg_bin 前置为脚本路径
    ffmpeg_bin = getattr(renderer, "ffmpeg_bin", None)
    if ffmpeg_bin is not None:
        args = [ffmpeg_bin, *args]
    try:
        runner.run(args, lambda _p: None, None, timeout_sec=60)
    except Exception:  # noqa: BLE001 - 封面失败不阻塞导出
        return False
    return cover_path.is_file()


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由发布三命令。"""
    repos = deps.repos
    p: dict[str, Any] = env.payload or {}

    if env.commandType == "CreatePlatformVariant":
        project_id = p.get("projectId") or env.projectId
        if not project_id:
            raise DispatchError("INVALID_ARGUMENT", "projectId required")
        prj = repos.conn.execute(
            "SELECT id FROM content_projects WHERE id=?", (project_id,)
        ).fetchone()
        if prj is None:
            raise DispatchError("NOT_FOUND", f"project {project_id!r} not found")
        platform = p.get("platform")
        if platform not in _PLATFORMS:
            raise DispatchError(
                "INVALID_ARGUMENT",
                f"platform must be one of {_PLATFORMS}, got {platform!r}",
            )
        title = p.get("title")
        if not title or not isinstance(title, str):
            raise DispatchError("INVALID_ARGUMENT", "title required")
        tags = p.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise DispatchError("INVALID_ARGUMENT", "tags must be a list of strings")

        video_version_id = p.get("videoVersionId")
        anchor_id = _resolve_anchor_version(repos, project_id, video_version_id)

        # 独立命名，避免与后续分支的 p.get("variantId")（Any|None）复用同名
        new_variant_id = f"pv_{uuid.uuid4().hex}"
        now = _now()
        repos.conn.execute(
            "INSERT INTO platform_variants "
            "(id, content_version_id, platform, title, body, tags, cover_text, "
            "validation_status, created_at, updated_at, project_id, video_version_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_variant_id, anchor_id, platform, title,
                str(p.get("body") or ""),
                json.dumps(tags, ensure_ascii=False),
                None, "draft", now, now,
                project_id, video_version_id,
            ),
        )
        repos.conn.commit()
        row = repos.conn.execute(
            "SELECT * FROM platform_variants WHERE id=?", (new_variant_id,)
        ).fetchone()
        return CommandResult(
            ok=True, commandId=env.commandId,
            detail={"variant": _row_to_variant(row)},
        )

    if env.commandType == "ListPlatformVariants":
        project_id = p.get("projectId") or env.projectId
        if not project_id:
            raise DispatchError("INVALID_ARGUMENT", "projectId required")
        rows = repos.conn.execute(
            "SELECT * FROM platform_variants WHERE project_id=? "
            "ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        return CommandResult(
            ok=True, commandId=env.commandId,
            detail={"variants": [_row_to_variant(r) for r in rows]},
        )

    if env.commandType == "ExportBundle":
        variant_id = p.get("variantId")
        if not variant_id:
            raise DispatchError("INVALID_ARGUMENT", "variantId required")
        row = repos.conn.execute(
            "SELECT * FROM platform_variants WHERE id=?", (variant_id,)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"variant {variant_id!r} not found")
        variant = _row_to_variant(row)

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        bundle_dir = (
            resolve_stepwork_home() / "exports" / f"bundle-{variant_id}-{ts}"
        )
        bundle_dir.mkdir(parents=True, exist_ok=True)

        # 视频：复制渲染 artifact（若有）
        video_src = _resolve_video_path(repos, variant["video_version_id"])
        video_file: str | None = None
        if video_src:
            try:
                shutil.copyfile(video_src, bundle_dir / "video.mp4")
                video_file = "video.mp4"
            except OSError:
                video_file = None

        # 封面：ffmpeg 取 1s 帧（不可用/失败即跳过，不阻塞导出）
        cover_file: str | None = None
        if video_file:
            if _extract_cover(deps, str(bundle_dir / "video.mp4"),
                              bundle_dir / "cover.jpg"):
                cover_file = "cover.jpg"

        (bundle_dir / "title.txt").write_text(variant["title"], encoding="utf-8")
        (bundle_dir / "body.txt").write_text(variant["body"], encoding="utf-8")
        (bundle_dir / "tags.txt").write_text(
            "\n".join(variant["tags"]), encoding="utf-8"
        )
        meta = {
            "schema_version": "1",
            "exported_at": _now(),
            "variant": variant,
            "files": {
                "video": video_file,
                "cover": cover_file,
                "title": "title.txt",
                "body": "body.txt",
                "tags": "tags.txt",
            },
        }
        (bundle_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return CommandResult(
            ok=True, commandId=env.commandId,
            detail={
                "bundle_path": str(bundle_dir),
                "variant_id": variant_id,
                "files": meta["files"],
            },
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by publish handler",
    )
