"""``IllustrateScenes`` 命令处理（S2 配图阶段）。

逐幕调 :class:`~worker.runtime.providers.image.base.ImageProvider` 生成配图
→ 回填 ``video_scenes.image_uri``。

**失败策略（与配音不同，是刻意的）**：任一幕失败 → 任务进 ``FAILED`` 并抛出
可读错误，**已成功的幕照常落库**。理由是生图要花钱：全部回滚会让一次限流
白烧掉 N 张图的钱；而「静默出空片」更是 S2 验收明令禁止的
（「生图失败时任务进入 FAILED 且错误信息可读，不是静默空片」）。

因此：

- 失败的幕保留原值（旧图或 NULL），**绝不清成 NULL** —— 那是把已有产出弄丢；
- 重跑默认**跳过已有图**的幕（``force=true`` 才重生成），避免重复计费。

**接口先于厂商**：本文件只依赖 :class:`ImageProvider` 协议，不 import 任何
具体厂商。StepFun 生图 2026-10-10 下线、选型未定，换厂商不需要动这里。
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import content_job, finish_job, transition
from worker.runtime.models import CommandEnvelope, CommandResult, JobStage, JobState
from worker.runtime.providers.resolve import image_provider_from_hint

#: 默认美术风格（与 RenderSpec.art_style 同域；S3 风格层会正式消费它）
_DEFAULT_STYLE = "xiaohei"


def _build_prompt(scene_text: str, style: str, extra: str | None) -> str:
    """把「幕文本 + 美术风格」拼成画面提示词。

    刻意**不**在这里做花活（不调 AI 扩写）：配图提示词是创作决策，
    交给调用方用 ``promptExtra`` 精确控制；默认值只保证「图文相关」。
    """
    head = f"{style} 风格插画，画面内容：{scene_text}"
    return f"{head}。{extra}" if extra else head


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``IllustrateScenes``。"""
    payload = dict(env.payload)
    version_id = payload.get("versionId") or payload.get("version_id")
    if not version_id:
        raise DispatchError("INVALID_ARGUMENT", "versionId required")
    repos = deps.repos
    repos.workspaces.ensure(env.workspaceId)
    project_id = env.projectId or repos.projects.get_or_create_default(
        env.workspaceId
    ).id
    src = repos.content_versions.get(str(version_id))
    if src is None or src.project_id != project_id:
        raise DispatchError("NOT_FOUND", f"version {version_id} not found")

    scenes = repos.video_scenes.list_by_version(str(version_id))
    if not scenes:
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"version {version_id} has no scenes (run GenerateScript or "
            "SaveVideoScenes first)",
        )

    image = image_provider_from_hint(payload.get("imageProvider")) or deps.image
    if image is None:
        raise DispatchError(
            "UNAVAILABLE",
            "image provider not configured (set STEPWORK_IMAGE_PROVIDER; "
            "vendor selection is still open — StepFun image gen EOL 2026-10-10)",
        )

    style = str(payload.get("style") or _DEFAULT_STYLE)
    extra = payload.get("promptExtra")
    out_dir = str(payload.get("outDir") or os.path.join(
        tempfile.gettempdir(), "stepwork_images"
    ))
    size = payload.get("size")  # {"width":..,"height":..}，交给 provider
    force = bool(payload.get("force", False))

    async with content_job(
        repos,
        job_type="illustrate_scenes",
        stage=JobStage.ILLUSTRATING,
        env=env,
        fail_code="ILLUSTRATE_FAILED",
        notify=deps.notify,
    ) as ctx:
        done: list[Any] = []
        failed: list[dict[str, Any]] = []
        skipped: list[int] = []
        total = len(scenes)
        for i, scene in enumerate(scenes):
            if scene.image_uri and not force:
                # 已有图且未强制 → 跳过（生图要钱，重跑不该重复计费）
                skipped.append(scene.seq)
                continue
            text = (scene.text or "").strip()
            if not text:
                skipped.append(scene.seq)
                continue
            opts: dict[str, Any] = {"out_dir": out_dir, "style": style}
            if isinstance(size, dict):
                opts.update(size)
            try:
                scene.image_uri = await image.generate(
                    _build_prompt(text, style, extra), opts
                )
            except DispatchError:
                raise
            except Exception as e:  # noqa: BLE001 - 厂商异常统一转译
                failed.append({"seq": scene.seq, "error": f"{type(e).__name__}: {e}"})
                continue
            done.append(scene)
            ctx.progress(0.05 + 0.85 * ((i + 1) / total), JobStage.ILLUSTRATING)

        if done:
            repos.video_scenes.apply_images(str(version_id), done)

        if failed:
            # 已成功的落库（不回滚、不重复计费），但任务必须 FAILED 且错误可读。
            #
            # 刻意**不抛 DispatchError**：dispatch 转译 DispatchError 时会丢掉
            # job_id（`CommandResult(ok=False, commandId=..., error=...)`），
            # 前端就看不到是哪个任务挂了。显式返回能带上 job_id。
            message = (
                f"ILLUSTRATE_FAILED: {len(failed)}/{total} scenes failed: "
                + "; ".join(f"seq={f['seq']} {f['error']}" for f in failed[:5])
            )
            transition(
                repos,
                ctx.job.id,
                JobState.FAILED,
                error=message[:200],
                stage=JobStage.ILLUSTRATING,
            )
            return CommandResult(
                ok=False,
                commandId=env.commandId,
                job_id=ctx.job.id,
                error=message,
            )

        finish_job(
            repos,
            ctx.job,
            stage=JobStage.ILLUSTRATING,
            artifact_ids=[s.image_uri for s in done if s.image_uri],
            notify=deps.notify,
        )
        job_id = ctx.job.id

    return CommandResult(
        ok=True,
        commandId=env.commandId,
        job_id=job_id,
        artifact_ids=[str(version_id)],
        detail={
            "versionId": str(version_id),
            "style": style,
            "count": len(done),
            "skippedSeqs": skipped,
            "scenes": [
                {"id": s.id, "seq": s.seq, "imageUri": s.image_uri} for s in done
            ],
        },
    )
