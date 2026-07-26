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

from worker.runtime.audit import EVENT_BUNDLE_EXPORTED, record_event
from worker.runtime.cleanup import resolve_stepwork_home
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.handlers.approvals import content_hash as approval_content_hash
from worker.runtime.handlers.approvals import create_request as create_approval
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


#: PRD-PUB-005 发布任务状态机
_PUBLISH_STATES: frozenset[str] = frozenset(
    {"awaiting_approval", "ready", "publishing", "published", "failed", "cancelled"}
)


def _publish_job_to_dict(row: Any) -> dict[str, Any]:
    """publish_jobs 行 → 出参（含状态、时间与证据 artifact）。"""
    try:
        evidence = json.loads(row["evidence_artifact_ids"] or "[]")
    except (TypeError, ValueError):
        evidence = []
    return {
        "id": str(row["id"]),
        "platform_variant_id": str(row["platform_variant_id"]),
        "social_account_id": row["social_account_id"],
        "plugin_id": row["plugin_id"],
        "plugin_version": row["plugin_version"],
        "state": str(row["state"]),
        "approval_id": row["approval_id"],
        # PRD-PUB-005：证据（脱敏截图等 artifact id）
        "evidence_artifact_ids": evidence,
        "remote_content_id": row["remote_content_id"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _approval_is_approved(conn: Any, approval_id: str) -> bool:
    """授权是否处于 approved 状态（未过期、未用掉）。"""
    row = conn.execute(
        "SELECT status FROM approval_requests WHERE id=?", (approval_id,)
    ).fetchone()
    return row is not None and str(row["status"]) == "approved"


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

    if env.commandType == "RequestPublishAuthorization":
        # PRD-PUB-004「一次性发布授权：授权与账号、内容哈希、插件版本绑定」。
        # publish_jobs.approval_id 列 0003 就有，但全仓无任何写入方。
        variant_id = p.get("variantId") or p.get("variant_id")
        if not variant_id:
            raise DispatchError("INVALID_ARGUMENT", "variantId required")
        row = repos.conn.execute(
            "SELECT * FROM platform_variants WHERE id=?", (str(variant_id),)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"variant {variant_id!r} not found")

        account_id = p.get("socialAccountId") or p.get("social_account_id")
        plugin_id = p.get("pluginId") or p.get("plugin_id")
        plugin_version = p.get("pluginVersion") or p.get("plugin_version")

        # 绑定内容：变体的标题/正文/标签一起入哈希，任一改动都会让授权失效
        bound_content = {
            "variant_id": str(variant_id),
            "platform": row["platform"],
            "title": row["title"],
            "body": row["body"],
            "tags": row["tags"],
            "social_account_id": account_id,
            "plugin_id": plugin_id,
            "plugin_version": plugin_version,
        }
        approval_id = create_approval(
            repos.conn,
            actor=f"{(env.actor or {}).get('type', 'user')}:"
            f"{(env.actor or {}).get('id', 'unknown')}",
            action_type="PublishVariant",
            target=str(variant_id),
            requested_scope="once",
            risk_summary=(
                f"发布到 {row['platform']}"
                + (f"（账号 {account_id}）" if account_id else "")
                + "；发布后内容将对外可见，且平台侧撤回需自行操作。"
            ),
            payload=bound_content,
        )

        job_id = f"pubjob_{uuid.uuid4().hex}"
        now = _now()
        repos.conn.execute(
            "INSERT INTO publish_jobs "
            "(id, platform_variant_id, social_account_id, plugin_id, plugin_version, "
            "state, approval_id, evidence_artifact_ids, remote_content_id, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id, str(variant_id), account_id, plugin_id, plugin_version,
                "awaiting_approval", approval_id, "[]", None, now, now,
            ),
        )
        repos.conn.commit()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "publish_job_id": job_id,
                "approval_id": approval_id,
                "content_hash": approval_content_hash(bound_content),
                "state": "awaiting_approval",
            },
        )

    if env.commandType == "RecordPublishResult":
        # PRD-PUB-005「发布结果验证和证据：保存状态、时间和脱敏截图」。
        # 独立命名，避免与上文生成的 str job_id 复用同名（mypy no-redef 同型）
        target_job = p.get("publishJobId") or p.get("publish_job_id")
        state = str(p.get("state") or "")
        if not target_job:
            raise DispatchError("INVALID_ARGUMENT", "publishJobId required")
        if state not in _PUBLISH_STATES:
            raise DispatchError(
                "INVALID_ARGUMENT",
                f"state must be one of {sorted(_PUBLISH_STATES)}",
            )
        job_row = repos.conn.execute(
            "SELECT * FROM publish_jobs WHERE id=?", (str(target_job),)
        ).fetchone()
        if job_row is None:
            raise DispatchError("NOT_FOUND", f"publish job {target_job!r} not found")

        # 发布必须先获批（否则「一次性授权」形同虚设）
        if state == "published":
            approval_id = job_row["approval_id"]
            if not approval_id or not _approval_is_approved(repos.conn, approval_id):
                raise DispatchError(
                    "FORBIDDEN", "publish requires an approved authorization"
                )

        evidence = p.get("evidenceArtifactIds") or p.get("evidence_artifact_ids") or []
        if not isinstance(evidence, list) or not all(
            isinstance(e, str) for e in evidence
        ):
            raise DispatchError(
                "INVALID_ARGUMENT", "evidenceArtifactIds must be a list of strings"
            )
        repos.conn.execute(
            "UPDATE publish_jobs SET state=?, evidence_artifact_ids=?, "
            "remote_content_id=?, updated_at=? WHERE id=?",
            (
                state,
                json.dumps(evidence, ensure_ascii=False),
                p.get("remoteContentId") or p.get("remote_content_id"),
                _now(),
                str(target_job),
            ),
        )
        # 一次性授权用掉即作废（PRD-PUB-004）
        if state == "published" and job_row["approval_id"]:
            repos.conn.execute(
                "UPDATE approval_requests SET status='consumed' "
                "WHERE id=? AND requested_scope='once'",
                (job_row["approval_id"],),
            )
        repos.conn.commit()
        updated = repos.conn.execute(
            "SELECT * FROM publish_jobs WHERE id=?", (str(target_job),)
        ).fetchone()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"publish_job": _publish_job_to_dict(updated)},
        )

    if env.commandType == "ListPublishJobs":
        pid = p.get("projectId") or p.get("project_id") or env.projectId
        sql = (
            "SELECT pj.* FROM publish_jobs pj "
            "JOIN platform_variants pv ON pv.id = pj.platform_variant_id"
        )
        args: list[Any] = []
        if pid:
            sql += " WHERE pv.project_id=?"
            args.append(str(pid))
        sql += " ORDER BY pj.created_at DESC"
        rows = repos.conn.execute(sql, tuple(args)).fetchall()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"publish_jobs": [_publish_job_to_dict(r) for r in rows]},
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by publish handler",
    )
