"""``RenderSource`` 命令处理（W6）。

职责（对齐 transcribe_source）：
1. 解析 RenderSpec（源 ContentVersion / 模板 / TTS 引擎）
2. 创建 RenderJob（PENDING → RUNNING），获取租约（kill -9 恢复用）
3. TTS 合成旁白（user_audio 引擎则直接用用户录音 uri）
4. 调 Renderer 渲染（9:16 字幕/背景）→ 进度/取消/重试
5. 渲染产物作为 ``content_versions(video_draft)`` 落库 → 回写 artifact id

生命周期骨架（workspace/创建/租约/RUNNING/SUCCEEDED/FAILED）经
``content_job`` 去重；本文件只保留渲染特有的进度去抖、取消注册、
TTS 临时文件清理与 FFmpeg 特定错误分支。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import content_job, persist_content_version, transition
from worker.runtime.jobs.cancel import clear, register
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
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


def _truncate_meta_json(meta: VideoDraftMeta) -> str:
    """序列化 ``VideoDraftMeta`` 并保证落库 JSON 始终合法。

    原实现直接对 ``model_dump_json()`` 的字符串做 ``[:20000]`` 切片，可能
    截断在 token 中间导致回读失败（T4）。改为：先反序列化为 dict，超长时
    删除非必要的 ``producer`` 再对最长字符串字段做字符级裁剪，每次都重新
    ``json.dumps``，因此结果一定是合法 JSON。
    """
    obj = json.loads(meta.model_dump_json())
    encoded = json.dumps(obj, ensure_ascii=False)
    if len(encoded) <= _MAX_DRAFT_META_CHARS:
        return encoded
    # 丢弃最大的非必要对象字段（producer 元数据）
    obj.pop("producer", None)
    encoded = json.dumps(obj, ensure_ascii=False)
    # 仍超长则对剩余字符串字段做字符级裁剪，循环收敛（始终重新序列化 → 合法 JSON）
    while len(encoded) > _MAX_DRAFT_META_CHARS:
        str_fields = [(k, v) for k, v in obj.items() if isinstance(v, str) and len(v) > 0]
        if not str_fields:
            break
        longest_key = max(str_fields, key=lambda kv: len(kv[1]))[0]
        overflow = len(encoded) - _MAX_DRAFT_META_CHARS
        obj[longest_key] = obj[longest_key][: max(0, len(obj[longest_key]) - overflow)]
        encoded = json.dumps(obj, ensure_ascii=False)
    return encoded


def _delete_uri(uri: str) -> None:
    """删除 ``file://`` 或裸路径指向的本地文件（忽略不存在/无权限）。"""
    path = uri[7:] if uri.startswith("file://") else uri
    try:
        os.remove(path)
    except OSError:
        pass


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

    # 输入校验（job 创建前）→ 保留干净的错误码
    if spec.tts_engine.value == "user_audio":
        if not spec.user_audio_uri:
            raise DispatchError(
                "INVALID_ARGUMENT", "user_audio_uri required for user_audio engine"
            )
    else:
        if deps.tts is None:
            raise DispatchError("UNAVAILABLE", "tts provider not configured")

    cancel_event = threading.Event()
    tts_out_dir = os.path.join(tempfile.gettempdir(), "stepwork_tts")
    # 仅追踪本地合成的 TTS 产物，供 finally 清理；user_audio 不删除用户文件
    generated_audio: str | None = None
    async with content_job(
        repos,
        job_type="render_source",
        stage=JobStage.RENDERING,
        env=env,
        fail_code="RENDER_FAILED",
        lease="render_source",
    ) as ctx:
        register(ctx.job.id, cancel_event)
        try:
            if spec.tts_engine.value == "user_audio":
                audio_uri = spec.user_audio_uri
            else:
                audio_uri = await deps.tts.synthesize(
                    src.content, {"out_dir": tts_out_dir}
                )
                generated_audio = audio_uri

            spec.caption_text = (src.content or "")[:200]

            # renderer.render 是阻塞的同步调用（跑 ffmpeg），放入 worker 线程，
            # 避免阻塞主事件循环。进度回调会跨线程触发，因此通过主线程的 loop
            # 把 DB 写入（transition）调度回主线程执行，确保所有 DB 访问留在
            # 创建连接的主线程（db_conn 使用 check_same_thread=True）。
            # 进度写去抖（T5）：每 ~5% 或 ≥1s 才提交一次 UPDATE，抑制写放大。
            loop = asyncio.get_running_loop()
            _last = {"progress": -1.0, "ts": 0.0}

            def _progress(prog: float) -> None:
                now = time.monotonic()
                if (prog - _last["progress"]) >= 0.05 or (now - _last["ts"]) >= 1.0:
                    captured = prog

                    def _commit(p: float) -> None:
                        transition(
                            repos,
                            ctx.job.id,
                            JobState.RUNNING,
                            progress=p,
                            stage=JobStage.RENDERING,
                        )

                    loop.call_soon_threadsafe(_commit, captured)
                    _last["progress"] = prog
                    _last["ts"] = now

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
            # T4：保证落库 JSON 合法，绝不截断在 token 中间
            content = _truncate_meta_json(meta)
            cv_id = persist_content_version(
                repos,
                ctx.job,
                project_id=ctx.project_id,
                content=content,
                content_type="video_draft",
                content_hash=_video_content_hash(result.video_uri),
                producer=meta.producer,
                stage=JobStage.RENDERING,
                parent_version_id=spec.source_version_id,
            )
            return CommandResult(
                ok=True,
                commandId=env.commandId,
                job_id=ctx.job.id,
                artifact_ids=[cv_id],
                detail={
                    "video_uri": result.video_uri,
                    "template": result.template,
                    "tts_engine": result.tts_engine,
                },
            )
        except FFmpegCancelled:
            transition(repos, ctx.job.id, JobState.CANCELLED, stage=JobStage.RENDERING)
            return CommandResult(
                ok=False, commandId=env.commandId, job_id=ctx.job.id, error="CANCELLED"
            )
        except FFmpegUnavailable:
            transition(repos, ctx.job.id, JobState.FAILED, error="UNAVAILABLE")
            raise DispatchError("UNAVAILABLE", "ffmpeg not available") from None
        except FFmpegFailed as e:
            transition(
                repos, ctx.job.id, JobState.FAILED, error=f"FFMPEG_FAILED:{e.code}"
            )
            raise DispatchError("RENDER_FAILED", f"ffmpeg exit {e.code}") from None
        except DispatchError:
            raise
        # 其它未预期异常由 content_job 上下文统一转译为 FAILED + RENDER_FAILED
        finally:
            clear(ctx.job.id)
            if generated_audio:
                _delete_uri(generated_audio)  # T5：清理孤儿 TTS 音频
