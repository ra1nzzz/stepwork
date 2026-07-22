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
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import content_job, persist_content_version
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
    JobStage,
)
from worker.runtime.providers.resolve import ai_provider_from_hint


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
            },
            stage=JobStage.ANALYZING,
        )

    return CommandResult(
        ok=True,
        commandId=env.commandId,
        job_id=ctx.job.id,
        artifact_ids=[cv_id],
        detail={
            "transcript_version_id": tv_id,
            "provider": report.provider,
            "model": report.model,
            "sentiment": report.sentiment,
            "topic_count": len(report.topics),
            "confidence": report.confidence,
        },
    )
