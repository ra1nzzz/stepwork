"""``GenerateScript`` 命令处理（W5）。

职责：
1. 解析 ScriptSpec（proposal_version_id / topic_id / outline / style）
2. 载入选定 TopicProposal 的 angles
3. 调 AI Provider（携带 Script schema）→ 解析为脚本内容
4. 落 ``content_versions(script)``，parent = proposal 版
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import content_job, persist_content_version
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
    JobStage,
    ScriptSpec,
)
from worker.runtime.providers.resolve import ai_provider_from_hint
from worker.runtime.script.parse import parse_script
from worker.runtime.script.prompt import SCRIPT_SCHEMA, build_script_prompt


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``GenerateScript``。"""
    repos = deps.repos
    try:
        spec = ScriptSpec(**env.payload)
    except Exception as e:
        raise DispatchError("INVALID_ARGUMENT", f"bad script spec: {e}") from None

    # 选定 TopicProposal 版（若提供）→ 取 angles。
    # 解析放在输入校验阶段（job 创建前），坏 content 直接转译为干净的
    # DispatchError，而非泄漏为泛化 internal:（T5）。
    angles: list[dict[str, Any]] = []
    parent_id = spec.proposal_version_id
    if spec.proposal_version_id:
        pv = repos.content_versions.get(spec.proposal_version_id)
        if pv is None or pv.content_type != "topic_proposal":
            raise DispatchError(
                "NOT_FOUND",
                f"proposal {spec.proposal_version_id} not found",
            )
        try:
            angles = json.loads(pv.content).get("angles", [])
        except Exception as e:
            raise DispatchError(
                "INVALID_ARGUMENT", f"bad proposal content: {e}"
            ) from None

    ai = ai_provider_from_hint(spec.provider) or deps.ai
    if ai is None:
        raise DispatchError("UNAVAILABLE", "ai provider not configured")

    prompt = build_script_prompt(angles, spec.topic_id, spec.outline, spec.style)
    async with content_job(
        repos,
        job_type="script",
        stage=JobStage.SCRIPTING,
        env=env,
        fail_code="SCRIPT_FAILED",
    ) as ctx:
        raw = await ai.complete(prompt, SCRIPT_SCHEMA)
        script = parse_script(raw)
        content = json.dumps(script, ensure_ascii=False)
        cv_id = persist_content_version(
            repos,
            ctx.job,
            project_id=ctx.project_id,
            content=content,
            content_type="script",
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            producer={
                "kind": "ai-script",
                "provider": getattr(ai, "name", "unknown"),
                "model": getattr(ai, "model", "unknown"),
            },
            stage=JobStage.SCRIPTING,
            parent_version_id=parent_id,
        )
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        job_id=ctx.job.id,
        artifact_ids=[cv_id],
        detail={
            "title": script.get("title"),
            "parent": parent_id,
            # 供前端直接 seed 编辑器，无需额外 content-fetch 接口
            "script": script,
        },
    )
