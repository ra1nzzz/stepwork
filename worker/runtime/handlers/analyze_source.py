"""``AnalyzeSource`` 命令处理（W4，Batch 2）。

职责：
1. 解析输入（``transcript_version_id`` 指向的转写，或 ``text`` 直接文本）
2. 创建分析 job（PENDING → RUNNING）+ 租约
3. 构造 prompt → 调注入的 AI Provider（携带 analysis schema）
4. 解析为 ``AnalysisReport``（对照 schema 校验）
5. 作为 ``content_versions``（``analysis``）落库，job 标记 SUCCEEDED
"""

from __future__ import annotations

import hashlib
from typing import Any

from worker.runtime.analysis.prompt import build_analysis_prompt
from worker.runtime.analysis.report import parse_analysis_report
from worker.runtime.analysis.schema import ANALYSIS_SCHEMA
from worker.runtime.audit import build_invocation, record_provider_invocation
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import content_job, persist_content_version
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
    JobStage,
)
from worker.runtime.providers.resolve import ai_provider_from_hint


def _resolve_media_uri(repos: Any, p: dict[str, Any]) -> str:
    """精确模式的媒体源解析：``asset_id``（→ local_uri）或 ``media_uri``。"""
    asset_id = p.get("asset_id")
    if asset_id:
        asset = repos.source_assets.get(asset_id)
        if asset is None:
            raise DispatchError("NOT_FOUND", f"asset {asset_id} not found")
        return str(asset.local_uri)
    media_uri = p.get("media_uri")
    if not media_uri:
        raise DispatchError(
            "INVALID_ARGUMENT", "precise mode requires asset_id or media_uri"
        )
    return str(media_uri)


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``AnalyzeSource``。"""
    repos = deps.repos
    p: dict[str, Any] = env.payload

    # per-request provider 切换（前端 provider-switch 生效点）；
    # 无提示时回退到 bootstrap 注入的默认 provider。
    ai = ai_provider_from_hint(p.get("provider")) or deps.ai
    if ai is None:
        raise DispatchError("UNAVAILABLE", "ai provider not configured")

    # 解析输入文本
    text: str | None = None
    tv_id = p.get("transcript_version_id")
    if tv_id:
        cv = repos.content_versions.get(tv_id)
        if cv is None:
            raise DispatchError("NOT_FOUND", f"content_version {tv_id} not found")
        if cv.content_type != "transcript":
            raise DispatchError(
                "INVALID_ARGUMENT",
                "content_version must be type 'transcript'",
            )
        text = cv.content
    if not text:
        text = p.get("text")
    if not text:
        raise DispatchError(
            "INVALID_ARGUMENT", "transcript_version_id or text required"
        )

    brand = p.get("brand")
    source_meta: dict[str, Any] = {
        "text": text,
        "text_length": len(text),
        "has_brand": bool(brand),
    }

    # 精确分析（PRD-ANA-003）：在转写文本之上叠加场景切分时间线。
    # quick（默认）保持纯文本；precise 需媒体源 + 可用的场景切分器。
    mode = str(p.get("mode") or "quick").lower()
    scenes: list[dict[str, Any]] = []
    if mode == "precise":
        media_uri = _resolve_media_uri(repos, p)
        detector = deps.scene_detector
        if detector is None or not getattr(detector, "available", False):
            raise DispatchError("UNAVAILABLE", "scene detector not available")
        detected = await detector.detect(media_uri, p.get("scene_opts"))
        scenes = [s.model_dump() for s in detected]
        source_meta["scenes"] = scenes

    prompt = build_analysis_prompt(source_meta, brand)

    async with content_job(
        repos,
        job_type="analyze",
        stage=JobStage.ANALYZING,
        env=env,
        fail_code="ANALYSIS_FAILED",
        lease="analyze_source",
        payload={
            "transcript_version_id": tv_id,
            "provider": getattr(ai, "name", "unknown"),
        },
        notify=deps.notify,
    ) as ctx:
        raw = await ai.complete(prompt, ANALYSIS_SCHEMA)
        report = parse_analysis_report(raw)
        content = report.model_dump_json()
        cv_id = persist_content_version(
            repos,
            ctx.job,
            project_id=ctx.project_id,
            content=content,
            content_type="analysis",
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            producer={
                "kind": "ai-analysis",
                "provider": report.provider or getattr(ai, "name", "unknown"),
                "model": report.model,
                "schema_version": "analysis.schema.json",
                # 精确分析（PRD-ANA-003）：场景时间线随产物落库，供引用来源
                # 位置（PRD-ANA-005）；quick 模式为空 list。
                "mode": mode,
                "scenes": scenes,
            },
            stage=JobStage.ANALYZING,
            notify=deps.notify,
        )

    # 费用透明（Tranche 2）：detail.invocation + provider_invocation 审计行
    invocation = build_invocation(ai, len(prompt) + len(content))
    record_provider_invocation(repos.conn, env, invocation)
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        job_id=ctx.job.id,
        artifact_ids=[cv_id],
        detail={
            "transcript_version_id": tv_id,
            "provider": report.provider,
            "model": report.model,
            "mode": mode,
            "scene_count": len(scenes),
            "sentiment": report.sentiment,
            "topic_count": len(report.topics),
            "confidence": report.confidence,
            "invocation": invocation,
        },
    )
