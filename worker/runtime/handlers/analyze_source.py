"""``AnalyzeSource`` 命令处理（W4，Batch 2）。

职责：
1. 解析输入（``transcript_version_id`` 指向的转写，或 ``text`` 直接文本）
2. 创建分析 job（PENDING → RUNNING）+ 租约
3. 构造 prompt → 调注入的 AI Provider（携带 analysis schema）
4. 解析为 ``AnalysisReport``（对照 schema 校验）
5. 作为 ``content_versions``（``analysis``）落库，job 标记 SUCCEEDED
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from worker.runtime.analysis.prompt import build_analysis_prompt
from worker.runtime.analysis.report import parse_analysis_report
from worker.runtime.analysis.schema import ANALYSIS_SCHEMA
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import acquire, create_job, record_result, transition
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
    ContentVersion,
    JobStage,
    JobState,
)


def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``AnalyzeSource``。"""
    repos = deps.repos
    p: dict[str, Any] = env.payload

    repos.workspaces.ensure(env.workspaceId)
    project_id = env.projectId or repos.projects.get_or_create_default(
        env.workspaceId
    ).id

    ai = deps.ai
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

    job = create_job(
        repos,
        "analyze",
        {
            "transcript_version_id": tv_id,
            "provider": getattr(ai, "name", "unknown"),
        },
        stage=JobStage.ANALYZING,
    )
    job = transition(repos, job.id, JobState.RUNNING)
    acquire(repos.conn, job.id, owner="analyze_source", ttl_sec=600)

    try:
        raw = asyncio.run(ai.complete(prompt, ANALYSIS_SCHEMA))
        report = parse_analysis_report(raw)
    except Exception as e:  # 分析失败需转译为领域错误
        transition(repos, job.id, JobState.FAILED, error=str(e)[:200])
        raise DispatchError("ANALYSIS_FAILED", str(e)[:200]) from None

    cv = ContentVersion(
        project_id=project_id,
        content_type="analysis",
        content=report.model_dump_json(),
        content_hash=hashlib.sha256(
            report.model_dump_json().encode("utf-8")
        ).hexdigest(),
        producer={
            "kind": "ai-analysis",
            "provider": report.provider or getattr(ai, "name", "unknown"),
            "model": report.model,
            "schema_version": "analysis.schema.json",
        },
    )
    cv_id = repos.content_versions.insert(cv)
    transition(
        repos, job.id, JobState.SUCCEEDED,
        progress=1.0, error=None, stage=JobStage.ANALYZING,
    )
    record_result(repos, job.id, [cv_id])

    return CommandResult(
        ok=True,
        commandId=env.commandId,
        job_id=job.id,
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
