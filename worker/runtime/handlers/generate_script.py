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

from worker.runtime.audit import build_invocation, record_provider_invocation
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.handlers.brand import (
    brand_producer_fields,
    collect_banned_hits,
    format_brand_prompt_block,
    load_project_brand,
)
from worker.runtime.jobs import (
    content_job,
    persist_content_version,
    persist_script_scenes,
    transition,
)
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
    JobStage,
    JobState,
    ScriptSpec,
)
from worker.runtime.providers.resolve import ai_provider_from_hint
from worker.runtime.script.history import load_script_history
from worker.runtime.script.parse import parse_script
from worker.runtime.script.prompt import SCRIPT_SCHEMA, build_script_prompt
from worker.runtime.script.similarity import (
    SCRIPT_THRESHOLD,
    find_similar,
    hits_to_warnings,
    similarity_check_enabled,
)


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

    prompt = build_script_prompt(
        angles, spec.topic_id, spec.outline, spec.style, brand_block
    )
    async with content_job(
        repos,
        job_type="script",
        stage=JobStage.SCRIPTING,
        env=env,
        fail_code="SCRIPT_FAILED",
        notify=deps.notify,
    ) as ctx:
        ctx.progress(0.2, JobStage.SCRIPTING)
        # S4「禁用词零出现」：prompt 里已经要求不得使用，但模型可能不遵守。
        # 产出命中 → 自动重试一次（同 prompt 换一次采样）；仍命中 → 任务
        # FAILED 并**指名命中了哪些词**——绝不静默放行违规文本（那等于把
        # 风格禁令当成装饰），也不悄悄改写（改文字 = 改文案语义）。
        banned = (brand or {}).get("bannedExpressions") or []
        script: dict[str, Any] = {}
        raw: dict[str, Any] = {}
        banned_retried = False
        banned_hits: list[str] = []
        for attempt in range(2):
            raw = await ai.complete(prompt, SCRIPT_SCHEMA)
            script = parse_script(raw)
            banned_hits = collect_banned_hits(
                f"{script.get('title', '')}\n{script.get('body', '')}", banned
            )
            if not banned_hits:
                break
            if attempt == 0:
                banned_retried = True
                ctx.progress(0.5, JobStage.SCRIPTING)  # 一次重试的可见进度
        if banned_hits:
            message = (
                "SCRIPT_FAILED: generated script still uses banned "
                f"expressions after retry: {'、'.join(banned_hits[:5])}"
            )
            transition(
                repos,
                ctx.job.id,
                JobState.FAILED,
                error=message[:200],
                stage=JobStage.SCRIPTING,
            )
            # 刻意不抛 DispatchError：dispatch 转译会丢 job_id（见项目记忆）。
            return CommandResult(
                ok=False,
                commandId=env.commandId,
                job_id=ctx.job.id,
                error=message,
            )
        ctx.progress(0.8, JobStage.SCRIPTING)
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
                **brand_producer_fields(brand),
            },
            stage=JobStage.SCRIPTING,
            parent_version_id=parent_id,
            notify=deps.notify,
        )
        # S2：文案产出即落幕。分幕若只能靠外部手工灌，就是孤岛——配音/
        # 配图/渲染全都拿不到输入。正文为空时清空该版本的幕。
        #
        # 注意顺序：此刻 job 已被 persist_content_version 置为 SUCCEEDED，
        # 所以这里**不能再发 progress**（SUCCEEDED→RUNNING 是非法迁移，会被
        # progress() 静默吞成日志），失败也必须显式转成 DispatchError——
        # 否则落进通用异常分支会去走 SUCCEEDED→FAILED，把真实错误盖掉。
        try:
            scenes = persist_script_scenes(repos, cv_id, content)
        except Exception as e:  # noqa: BLE001 - 转成领域错误，避免非法状态迁移
            raise DispatchError(
                "SCRIPT_FAILED", f"persist scenes failed: {e}"
            ) from None
        scene_ids = [s.id for s in scenes]
    # PRD-SCR-005「相似度与原创性提醒」：与同账号历史脚本比对。
    # 措辞是「提醒」而非判定 —— PRD 明确要求不做法律结论。
    similarity_warnings: list[dict[str, Any]] = []
    history = (
        load_script_history(repos.conn, ctx.project_id, exclude_version_id=cv_id)
        if similarity_check_enabled(repos.conn, env.workspaceId)
        else []
    )
    if history:
        body_text = str(script.get("body") or script.get("text") or "")
        similarity_warnings = hits_to_warnings(
            # 长文本用 containment 查重：目标是检测整段复用，而不是
            # 检测同题材（同题材不同表达属正常创作，报警只是噪声）
            find_similar(
                body_text,
                history,
                threshold=SCRIPT_THRESHOLD,
                limit=3,
                metric="containment",
            ),
            "similar_script",
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
            "title": script.get("title"),
            "parent": parent_id,
            # 供前端直接 seed 编辑器，无需额外 content-fetch 接口
            "script": script,
            # S4：命中过禁用词并自动重试过（成功那次是干净的）；False = 首过
            "bannedRetried": banned_retried,
            # S2：幕已随版本落库（配音/配图的输入），前端可直接拿 id 排队
            "sceneCount": len(scene_ids),
            "sceneIds": scene_ids,
            "invocation": invocation,
            # PRD-SCR-005：相似历史脚本提醒（空列表表示未发现相似）
            "similarity_warnings": similarity_warnings,
        },
    )
