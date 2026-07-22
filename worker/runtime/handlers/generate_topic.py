"""``GenerateTopic`` 命令处理（W5）。

职责：
1. 解析 TopicProposalSpec（source_version_id / count / provider hint）
2. 创建生成 job（PENDING → RUNNING）+ 租约
3. 调 AI Provider（携带 TopicProposal schema）
4. 解析为 ``TopicProposal``（angles）→ 落 ``content_versions(topic_proposal)``
5. job 标记 SUCCEEDED
"""
from __future__ import annotations

import asyncio
import hashlib

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import create_job, record_result, transition
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
    ContentVersion,
    JobStage,
    JobState,
    TopicProposalSpec,
)
from worker.runtime.providers.resolve import ai_provider_from_hint
from worker.runtime.topic.parse import parse_topic_proposal
from worker.runtime.topic.prompt import TOPIC_SCHEMA, build_topic_prompt


def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``GenerateTopic``。"""
    repos = deps.repos
    try:
        spec = TopicProposalSpec(**env.payload)
    except Exception as e:
        raise DispatchError("INVALID_ARGUMENT", f"bad topic spec: {e}") from None

    repos.workspaces.ensure(env.workspaceId)
    project_id = env.projectId or repos.projects.get_or_create_default(
        env.workspaceId
    ).id

    # 解析源文本（来自 transcript / script 等既有 content_version）
    src = repos.content_versions.get(spec.source_version_id)
    if src is None:
        raise DispatchError(
            "NOT_FOUND", f"source version {spec.source_version_id} not found"
        )
    text = src.content

    ai = ai_provider_from_hint(spec.provider) or deps.ai
    if ai is None:
        raise DispatchError("UNAVAILABLE", "ai provider not configured")

    prompt = build_topic_prompt(text, spec.count)
    job = create_job(
        repos, "topic", env.payload, stage=JobStage.PROPOSING
    )
    job = transition(repos, job.id, JobState.RUNNING)
    try:
        raw = asyncio.run(ai.complete(prompt, TOPIC_SCHEMA))
        proposal = parse_topic_proposal(raw, spec.count)
    except Exception as e:
        transition(repos, job.id, JobState.FAILED, error=str(e)[:200])
        raise DispatchError("TOPIC_FAILED", str(e)[:200]) from None

    cv = ContentVersion(
        project_id=project_id,
        parent_version_id=spec.source_version_id,
        content_type="topic_proposal",
        content=proposal.model_dump_json(),
        content_hash=hashlib.sha256(
            proposal.model_dump_json().encode("utf-8")
        ).hexdigest(),
        producer={
            "kind": "ai-topic",
            "provider": getattr(ai, "name", "unknown"),
            "model": getattr(ai, "model", "unknown"),
        },
    )
    cv_id = repos.content_versions.insert(cv)
    transition(
        repos, job.id, JobState.SUCCEEDED,
        progress=1.0, stage=JobStage.PROPOSING,
    )
    record_result(repos, job.id, [cv_id])
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        job_id=job.id,
        artifact_ids=[cv_id],
        detail={
            "angle_count": len(proposal.angles),
            "source_version_id": spec.source_version_id,
            # 供前端直接渲染，无需额外 content-fetch 接口
            "angles": [a.model_dump() for a in proposal.angles],
        },
    )
