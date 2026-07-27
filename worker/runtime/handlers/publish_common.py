"""发布域的公共依赖（平台白名单、行转换、锚点解析、授权校验）。

handlers/publish.py 曾是一个 627 行、11 个 commandType 分支的巨型
if/elif 长链，新增命令只能继续往里塞。按子域拆开后，这里放三个子模块共用的
部分 —— 拆分的意义在于「改发布授权时不必翻过定时发布的代码」。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.handlers.approvals import content_hash as approval_content_hash
from worker.runtime.publish.platforms import PLATFORM_RULES
from worker.runtime.render.ffmpeg_runner import FFmpegRunner

# 平台白名单（PRD-PUB-001）。**从规则表派生**，不再手写一份 —— 此前这里
# 硬编码 ("douyin", "generic")，往 PLATFORM_RULES 里加平台后仍然创建不了
# 变体，两处不同步且报错信息完全看不出原因。
_PLATFORMS: tuple[str, ...] = tuple(sorted(PLATFORM_RULES))


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


def _authorization_error(
    conn: Any, approval_id: str | None, current_bound: dict[str, Any]
) -> str | None:
    """校验授权可用；不可用时返回原因，可用返回 ``None``。

    PRD-PUB-004「授权与账号、内容哈希、插件版本绑定」的**绑定必须在发布
    时真正生效**：只看 status 等于把哈希当装饰——申请授权 → 用户批准 →
    偷偷改标题正文 → 照样能发。这里三项都查：

    1. status 必须是 approved（未拒绝、未过期、未用掉）
    2. **内容哈希必须与当前变体一致**（改了内容授权即失效）
    3. 有效期必须未过（批准不等于永久有效）
    """
    if not approval_id:
        return "publish requires an approved authorization"
    row = conn.execute(
        "SELECT status, payload, expires_at FROM approval_requests WHERE id=?",
        (approval_id,),
    ).fetchone()
    if row is None:
        return "authorization not found"
    if str(row["status"]) != "approved":
        return f"authorization is {row['status']}, not approved"

    # 有效期：批准后同样受 expires_at 约束
    expires_at = row["expires_at"]
    if expires_at and str(expires_at) < _now():
        return "authorization expired"

    try:
        bound = json.loads(row["payload"]) if row["payload"] else {}
    except (TypeError, ValueError):
        bound = {}
    expected = bound.get("content_hash")
    if expected and expected != approval_content_hash(current_bound):
        return (
            "content changed since authorization was granted; "
            "please request a new authorization"
        )
    return None


