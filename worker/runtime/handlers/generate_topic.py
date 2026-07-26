"""``GenerateTopic`` 命令处理（W5）。

职责：
1. 解析 TopicProposalSpec（source_version_id / count / provider hint）
2. 创建生成 job（PENDING → RUNNING）+ 租约
3. 调 AI Provider（携带 TopicProposal schema）
4. 解析为 ``TopicProposal``（angles）→ 落 ``content_versions(topic_proposal)``
5. job 标记 SUCCEEDED
"""
from __future__ import annotations

import hashlib
from typing import Any

from worker.runtime.audit import build_invocation, record_provider_invocation
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.handlers.brand import (
    brand_producer_fields,
    format_brand_prompt_block,
    load_project_brand,
)
from worker.runtime.jobs import content_job, persist_content_version
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
    JobStage,
    TopicProposalSpec,
)
from worker.runtime.providers.resolve import ai_provider_from_hint
from worker.runtime.script.history import load_topic_history
from worker.runtime.script.similarity import find_similar, hits_to_warnings
from worker.runtime.topic.parse import parse_topic_proposal
from worker.runtime.topic.prompt import TOPIC_SCHEMA, build_topic_prompt


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``GenerateTopic``。"""
    repos = deps.repos
    try:
        spec = TopicProposalSpec(**env.payload)
    except Exception as e:
        raise DispatchError("INVALID_ARGUMENT", f"bad topic spec: {e}") from None

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

    # 品牌画像注入（Tranche 2）：项目已关联 profile 时注入提示词约束，
    # 并在 producer 记录 brand_profile_id / brand_profile_updated_at。
    repos.workspaces.ensure(env.workspaceId)
    project_id = env.projectId or repos.projects.get_or_create_default(
        env.workspaceId
    ).id
    # PRD-BRD-002「生成时可选择启用」：关闭时即便项目已绑定品牌档
    # 也不加载、不注入，producer 也不记录品牌字段（可审计地表明未用）
    brand = load_project_brand(repos, project_id) if spec.use_brand_profile else None
    brand_block = format_brand_prompt_block(brand) if brand else None

    prompt = build_topic_prompt(text, spec.count, brand_block)
    async with content_job(
        repos,
        job_type="topic",
        stage=JobStage.PROPOSING,
        env=env,
        fail_code="TOPIC_FAILED",
        notify=deps.notify,
    ) as ctx:
        raw = await ai.complete(prompt, TOPIC_SCHEMA)
        proposal = parse_topic_proposal(raw, spec.count)
        content = proposal.model_dump_json()
        cv_id = persist_content_version(
            repos,
            ctx.job,
            project_id=ctx.project_id,
            content=content,
            content_type="topic_proposal",
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            producer={
                "kind": "ai-topic",
                "provider": getattr(ai, "name", "unknown"),
                "model": getattr(ai, "model", "unknown"),
                **brand_producer_fields(brand),
            },
            stage=JobStage.PROPOSING,
            parent_version_id=spec.source_version_id,
            notify=deps.notify,
        )
    # PRD-SCR-004「历史选题重复提醒」：与本项目 + 同 BrandProfile 其它项目的
    # 历史角度比对，超阈值即提示。只提醒、不拦截（用户可能就是要做续集）。
    duplicate_warnings: list[dict[str, Any]] = []
    history = load_topic_history(repos.conn, ctx.project_id, exclude_version_id=cv_id)
    if history:
        for angle in proposal.angles:
            hits = find_similar(
                f"{angle.title} {angle.rationale}", history, limit=3
            )
            for warning in hits_to_warnings(hits, "duplicate_topic"):
                warning["angle_id"] = angle.id
                duplicate_warnings.append(warning)

    # 费用透明（Tranche 2）：detail.invocation + provider_invocation 审计行
    invocation = build_invocation(ai, len(prompt) + len(content))
    record_provider_invocation(repos.conn, env, invocation)
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        job_id=ctx.job.id,
        artifact_ids=[cv_id],
        detail={
            "angle_count": len(proposal.angles),
            "source_version_id": spec.source_version_id,
            # 供前端直接渲染，无需额外 content-fetch 接口
            "angles": [a.model_dump() for a in proposal.angles],
            "invocation": invocation,
            # PRD-SCR-004：相似历史选题提醒（空列表表示无重复）
            "duplicate_warnings": duplicate_warnings,
        },
    )
