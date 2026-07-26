"""``ExportEditTimeline``：导出可继续剪辑的时间线（PRD-REN-006）。

把项目里**别的工具没有的数据**导成标准时间线：精确分析检出的场景切点、
逐字稿的时间戳。用户拿去 DaVinci Resolve / Premiere / Final Cut 里接着剪，
而不是从零拉时间线。

格式选择的调研结论见 ``render/edit_export.py`` 的模块 docstring —— 一句话：
剪映 6+ 起草稿加密（现已 10.x），要写就得内置逆向解密，与本项目立场冲突；
OpenCut 目前没有可移植工程格式；故选 OTIO（开放标准，主流 NLE 都读）
+ EDL（最大公约数）。
"""

from __future__ import annotations

import json
from typing import Any

from worker.runtime.cleanup import resolve_stepwork_home
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult
from worker.runtime.render.edit_export import (
    DEFAULT_FPS,
    SUPPORTED_FORMATS,
    write_timeline,
)


def _latest_analysis_scenes(conn: Any, project_id: str) -> list[dict[str, Any]]:
    """取最近一次**精确分析**的场景切点（quick 模式没有场景，返回空）。"""
    rows = conn.execute(
        "SELECT producer FROM content_versions "
        "WHERE project_id=? AND content_type='analysis' "
        "ORDER BY created_at DESC LIMIT 10",
        (project_id,),
    ).fetchall()
    for row in rows:
        try:
            producer = json.loads(row["producer"]) if row["producer"] else {}
        except (TypeError, ValueError):
            continue
        scenes = producer.get("scenes") if isinstance(producer, dict) else None
        if isinstance(scenes, list) and scenes:
            return [s for s in scenes if isinstance(s, dict)]
    return []


def _latest_transcript_segments(conn: Any, project_id: str) -> list[dict[str, Any]]:
    """取最近一版逐字稿的分段（用于生成时间线 marker）。"""
    row = conn.execute(
        "SELECT content FROM content_versions "
        "WHERE project_id=? AND content_type='transcript' "
        "ORDER BY created_at DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if row is None:
        return []
    try:
        parsed = json.loads(row["content"])
    except (TypeError, ValueError):
        return []
    segments = parsed.get("segments") if isinstance(parsed, dict) else None
    return [s for s in (segments or []) if isinstance(s, dict)]


def _resolve_media(conn: Any, project_id: str, asset_id: str | None) -> tuple[str, float]:
    """解析源素材路径与帧率。

    优先用显式给的 assetId；否则取该项目最新的一条素材 —— 时间线得指向
    真实文件，指不到就没意义。
    """
    if asset_id:
        row = conn.execute(
            "SELECT local_uri, metadata FROM source_assets WHERE id=? AND project_id=?",
            (asset_id, project_id),
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"asset {asset_id!r} not found in project")
    else:
        row = conn.execute(
            "SELECT local_uri, metadata FROM source_assets "
            "WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if row is None:
            raise DispatchError(
                "INVALID_ARGUMENT", "项目内没有素材，无法导出时间线"
            )
    uri = str(row["local_uri"] or "")
    fps = DEFAULT_FPS
    try:
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        if isinstance(meta, dict) and meta.get("fps"):
            fps = float(meta["fps"])
    except (TypeError, ValueError):
        pass
    return uri, fps


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    if env.commandType != "ExportEditTimeline":
        raise DispatchError(
            "UNKNOWN_COMMAND",
            f"commandType {env.commandType!r} not handled by export_timeline handler",
        )

    payload = env.payload or {}
    project_id = str(payload.get("projectId") or payload.get("project_id") or env.projectId or "")
    if not project_id:
        raise DispatchError("INVALID_ARGUMENT", "projectId required")
    fmt = str(payload.get("format") or "otio").lower()
    if fmt not in SUPPORTED_FORMATS:
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"format must be one of {SUPPORTED_FORMATS}, got {fmt!r}",
        )

    conn = deps.repos.conn
    prj = conn.execute(
        "SELECT title FROM content_projects WHERE id=?", (project_id,)
    ).fetchone()
    if prj is None:
        raise DispatchError("NOT_FOUND", f"project {project_id!r} not found")

    asset_id = payload.get("assetId") or payload.get("asset_id")
    media_path, fps = _resolve_media(conn, project_id, str(asset_id) if asset_id else None)
    scenes = _latest_analysis_scenes(conn, project_id)
    segments = _latest_transcript_segments(conn, project_id)

    out_dir = resolve_stepwork_home() / "exports" / f"timeline-{project_id}"
    target = write_timeline(
        out_dir,
        fmt=fmt,
        name=str(prj["title"] or project_id),
        media_path=media_path,
        scenes=scenes,
        segments=segments,
        fps=fps,
    )
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        detail={
            "path": str(target),
            "format": fmt,
            "scene_count": len(scenes),
            "marker_count": len(segments),
            # 没跑过精确分析时只有一个整段 clip，如实告知而不是假装切好了
            "note": (
                "场景切点来自精确分析"
                if scenes
                else "项目未跑过精确分析，时间线为整段单片段"
            ),
        },
    )
