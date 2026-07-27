"""平台变体：创建 / 列表 / 导出包 / 填充包（PRD-PUB-001/002/003）。

主稿（content_versions）**绝不修改** —— variant 是独立行，仅引用版本。
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from typing import Any

from worker.runtime.audit import EVENT_BUNDLE_EXPORTED, record_event
from worker.runtime.cleanup import resolve_stepwork_home
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.handlers.publish_common import (
    _PLATFORMS,
    _extract_cover,
    _now,
    _resolve_anchor_version,
    _resolve_video_path,
    _row_to_variant,
)
from worker.runtime.models import CommandEnvelope, CommandResult
from worker.runtime.publish import schedule
from worker.runtime.publish.platforms import build_fill_package


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
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
        # PRD §14 埋点：导出（此前 ExportBundle 无 job 也无 audit）
        record_event(
            repos.conn, env, EVENT_BUNDLE_EXPORTED,
            {"variant_id": str(variant_id), "bundle_path": str(bundle_dir)},
        )
        return CommandResult(
            ok=True, commandId=env.commandId,
            detail={
                "bundle_path": str(bundle_dir),
                "variant_id": variant_id,
                "files": meta["files"],
            },
        )

    if env.commandType == "BuildPlatformFillPackage":
        # PRD-PUB-003「不点击最终发布即可完成填充」+ ADR-008 FILL_AND_PREVIEW。
        # worker 只负责产出**内容与约束**；真正驱动浏览器 DOM 的是 publisher
        # 插件（需要用户已登录的浏览器会话），不在 worker 内实现。
        fill_variant_id = p.get("variantId") or p.get("variant_id")
        if not fill_variant_id:
            raise DispatchError("INVALID_ARGUMENT", "variantId required")
        row = repos.conn.execute(
            "SELECT * FROM platform_variants WHERE id=?", (str(fill_variant_id),)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"variant {fill_variant_id!r} not found")

        variant = _row_to_variant(row)
        video_path = _resolve_video_path(repos, variant.get("video_version_id"))
        cover = p.get("coverPath") or p.get("cover_path")
        # 定时发布：给了时间就在包里带上 schedule 段，供插件填平台自带的
        # 定时字段（原生模式）；不给则是立即发布，行为与此前完全一致
        raw_when = p.get("scheduledAt") or p.get("scheduled_at")
        try:
            when = schedule.parse_scheduled_at(str(raw_when)) if raw_when else None
        except ValueError as e:
            raise DispatchError("INVALID_ARGUMENT", f"scheduledAt 解析失败：{e}") from e
        package = build_fill_package(
            variant=variant,
            video_path=video_path,
            cover_path=str(cover) if cover else None,
            scheduled_at=when,
        )
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"fill_package": package},
        )


    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by this publish submodule",
    )
