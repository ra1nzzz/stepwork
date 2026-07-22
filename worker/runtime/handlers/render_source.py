"""``RenderSource`` 命令处理（W6）。

职责（对齐 transcribe_source）：
1. 解析 RenderSpec（源 ContentVersion / 模板 / TTS 引擎）
2. 创建 RenderJob（PENDING → RUNNING），获取租约（kill -9 恢复用）
3. TTS 合成旁白（user_audio 引擎则直接用用户录音 uri）
4. 调 Renderer 渲染（9:16 字幕/背景）→ 进度/取消/重试
5. 渲染产物作为 ``content_versions(video_draft)`` 落库 → 回写 artifact id
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import threading
from pathlib import Path

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import acquire, create_job, record_result, transition
from worker.runtime.jobs.cancel import clear, register
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
    ContentVersion,
    JobStage,
    JobState,
    RenderSpec,
    VideoDraftMeta,
)
from worker.runtime.render.ffmpeg_runner import (
    FFmpegCancelled,
    FFmpegFailed,
    FFmpegUnavailable,
)

_MAX_DRAFT_META_CHARS = 20000


def _video_content_hash(video_uri: str) -> str:
    """对视频文件字节做 sha256，文件缺失时回退到路径哈希。"""
    try:
        return hashlib.sha256(Path(video_uri).read_bytes()).hexdigest()
    except (OSError, ValueError):
        return hashlib.sha256(video_uri.encode("utf-8")).hexdigest()


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``RenderSource``。"""
    repos = deps.repos
    try:
        spec = RenderSpec(**env.payload)
    except Exception as e:
        raise DispatchError("INVALID_ARGUMENT", f"bad render spec: {e}") from None

    repos.workspaces.ensure(env.workspaceId)
    project_id = env.projectId or repos.projects.get_or_create_default(
        env.workspaceId
    ).id

    src = repos.content_versions.get(spec.source_version_id)
    if src is None or src.project_id != project_id:
        raise DispatchError(
            "NOT_FOUND", f"source version {spec.source_version_id} not found"
        )

    renderer = deps.renderer
    if renderer is None:
        raise DispatchError("UNAVAILABLE", "renderer not configured")

    job = create_job(
        repos, "render_source", env.payload, stage=JobStage.RENDERING
    )
    job = transition(repos, job.id, JobState.RUNNING)
    acquire(repos.conn, job.id, owner="render_source", ttl_sec=600)

    cancel_event = threading.Event()
    register(job.id, cancel_event)
    tts_out_dir = os.path.join(tempfile.gettempdir(), "stepwork_tts")
    try:
        if spec.tts_engine.value == "user_audio":
            if not spec.user_audio_uri:
                raise DispatchError(
                    "INVALID_ARGUMENT", "user_audio_uri required for user_audio engine"
                )
            audio_uri = spec.user_audio_uri
        else:
            tts = deps.tts
            if tts is None:
                raise DispatchError("UNAVAILABLE", "tts provider not configured")
            audio_uri = await tts.synthesize(
                src.content, {"out_dir": tts_out_dir}
            )

        spec.caption_text = (src.content or "")[:200]

        # renderer.render 是阻塞的同步调用（跑 ffmpeg），放入 worker 线程，
        # 避免阻塞主事件循环。进度回调会跨线程触发，因此通过主线程的 loop
        # 把 DB 写入（transition）调度回主线程执行，确保所有 DB 访问留在
        # 创建连接的主线程（db_conn 使用 check_same_thread=True）。
        loop = asyncio.get_running_loop()

        def _progress(prog: float) -> None:
            loop.call_soon_threadsafe(
                lambda: transition(
                    repos,
                    job.id,
                    JobState.RUNNING,
                    progress=prog,
                    stage=JobStage.RENDERING,
                )
            )

        result = await asyncio.to_thread(
            renderer.render, spec, audio_uri, _progress, cancel_event
        )

        meta = VideoDraftMeta(
            video_uri=result.video_uri,
            duration_seconds=result.duration_seconds,
            template=result.template,
            tts_engine=result.tts_engine,
            resolution=spec.resolution,
            fps=spec.fps,
            source_version_id=spec.source_version_id,
            producer={
                "kind": "renderer",
                "provider": getattr(renderer, "name", "unknown"),
                "template": result.template,
            },
        )
        content = meta.model_dump_json()
        if len(content) > _MAX_DRAFT_META_CHARS:
            content = content[:_MAX_DRAFT_META_CHARS]
        cv = ContentVersion(
            project_id=project_id,
            parent_version_id=spec.source_version_id,
            content_type="video_draft",
            content=content,
            content_hash=_video_content_hash(result.video_uri),
            producer=meta.producer,
        )
        cv_id = repos.content_versions.insert(cv)
        transition(
            repos, job.id, JobState.SUCCEEDED,
            progress=1.0, stage=JobStage.RENDERING,
        )
        record_result(repos, job.id, [cv_id])
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            job_id=job.id,
            artifact_ids=[cv_id],
            detail={
                "video_uri": result.video_uri,
                "template": result.template,
                "tts_engine": result.tts_engine,
            },
        )
    except FFmpegCancelled:
        transition(repos, job.id, JobState.CANCELLED, stage=JobStage.RENDERING)
        return CommandResult(
            ok=False, commandId=env.commandId, job_id=job.id, error="CANCELLED"
        )
    except FFmpegUnavailable:
        transition(repos, job.id, JobState.FAILED, error="UNAVAILABLE")
        raise DispatchError("UNAVAILABLE", "ffmpeg not available") from None
    except FFmpegFailed as e:
        transition(repos, job.id, JobState.FAILED, error=f"FFMPEG_FAILED:{e.code}")
        raise DispatchError("RENDER_FAILED", f"ffmpeg exit {e.code}") from None
    except DispatchError:
        raise
    except Exception as e:
        transition(repos, job.id, JobState.FAILED, error=str(e)[:200])
        raise DispatchError("RENDER_FAILED", str(e)[:200]) from None
    finally:
        clear(job.id)
