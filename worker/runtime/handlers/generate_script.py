"""``GenerateScript`` 命令处理（W5）。

职责：
1. 解析 ScriptSpec（proposal_version_id / topic_id / outline / style）
2. 载入选定 TopicProposal 的 angles
3. 调 AI Provider（携带 Script schema）→ 解析为脚本内容
4. 落 ``content_versions(script)``，parent = proposal 版
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import create_job, record_result, transition
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
    ContentVersion,
    JobStage,
    JobState,
    ScriptSpec,
)
from worker.runtime.providers.resolve import ai_provider_from_hint
from worker.runtime.script.parse import parse_script
from worker.runtime.script.prompt import SCRIPT_SCHEMA, build_script_prompt


def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``GenerateScript``。"""
    repos = deps.repos
    try:
        spec = ScriptSpec(**env.payload)
    except Exception as e:
        raise DispatchError("INVALID_ARGUMENT", f"bad script spec: {e}") from None

    repos.workspaces.ensure(env.workspaceId)
    project_id = env.projectId or repos.projects.get_or_create_default(
        env.workspaceId
    ).id

    # 选定 TopicProposal 版（若提供）→ 取 angles
    angles: list[dict[str, Any]] = []
    parent_id = spec.proposal_version_id
    if spec.proposal_version_id:
        pv = repos.content_versions.get(spec.proposal_version_id)
        if pv is None or pv.content_type != "topic_proposal":
            raise DispatchError(
                "NOT_FOUND",
                f"proposal {spec.proposal_version_id} not found",
            )
        angles = json.loads(pv.content).get("angles", [])

    ai = ai_provider_from_hint(spec.provider) or deps.ai
    if ai is None:
        raise DispatchError("UNAVAILABLE", "ai provider not configured")

    prompt = build_script_prompt(angles, spec.topic_id, spec.outline, spec.style)
    job = create_job(
        repos, "script", env.payload, stage=JobStage.SCRIPTING
    )
    job = transition(repos, job.id, JobState.RUNNING)
    try:
        raw = asyncio.run(ai.complete(prompt, SCRIPT_SCHEMA))
        script = parse_script(raw)
    except Exception as e:
        transition(repos, job.id, JobState.FAILED, error=str(e)[:200])
        raise DispatchError("SCRIPT_FAILED", str(e)[:200]) from None

    content = json.dumps(script, ensure_ascii=False)
    cv = ContentVersion(
        project_id=project_id,
        parent_version_id=parent_id,
        content_type="script",
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        producer={
            "kind": "ai-script",
            "provider": getattr(ai, "name", "unknown"),
            "model": getattr(ai, "model", "unknown"),
        },
    )
    cv_id = repos.content_versions.insert(cv)
    transition(
        repos, job.id, JobState.SUCCEEDED,
        progress=1.0, stage=JobStage.SCRIPTING,
    )
    record_result(repos, job.id, [cv_id])
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        job_id=job.id,
        artifact_ids=[cv_id],
        detail={"title": script.get("title"), "parent": parent_id},
    )
