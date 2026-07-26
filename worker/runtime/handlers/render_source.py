"""``RenderSource`` 命令处理（W6 + Tranche 2 渲染产物）。

职责（对齐 transcribe_source）：
1. 解析 RenderSpec（源 ContentVersion / 模板 / TTS 引擎）
2. 创建 RenderJob（PENDING → RUNNING），获取租约（kill -9 恢复用）
3. TTS 合成旁白（user_audio 引擎则直接用用户录音 uri）
4. 调 Renderer 渲染（9:16 字幕/背景）→ 进度/取消/重试
5. 渲染产物作为 ``content_versions(video_draft)`` 落库 → 回写 artifact id

Tranche 2（PRD-REN-001/003）：
- 生成 ``.srt`` 字幕 sidecar（与 mp4 同目录，按音频总时长等比分配）
- TTS 音频**不再删除**，作为 artifact 保留并登记进 VideoDraftMeta
- 成功 detail 增加 ``artifacts: {video, subtitles, audio}``（绝对路径）
  与 ``invocation``（费用透明）

生命周期骨架（workspace/创建/租约/RUNNING/SUCCEEDED/FAILED）经
``content_job`` 去重；本文件只保留渲染特有的进度去抖、取消注册与
FFmpeg 特定错误分支。
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
from typing import Any

from worker.runtime.audit import build_invocation, record_provider_invocation
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import (
    content_job,
    emit_job_progress,
    persist_content_version,
    transition,
)
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
from worker.runtime.render.subtitles import (
    probe_audio_duration,
    write_srt_sidecar,
)
from worker.runtime.render.templates import resolve_resolution, resolve_template

_MAX_DRAFT_META_CHARS = 20000


def _abs_or_none(path: str | None) -> str | None:
    """同步 helper：转绝对路径（避免 async handler 内触发 ASYNC240）。"""
    return os.path.abspath(path) if path else None


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


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``RenderSource``。"""
    repos = deps.repos
    payload = dict(env.payload)
    # PRD-REN-005：可用 aspect（9:16 / 16:9 / 1:1）代替显式 resolution；
    # 二者都给时以显式 resolution 为准（更精确）。
    aspect = payload.pop("aspect", None)
    if aspect is not None and "resolution" not in payload:
        try:
            payload["resolution"] = resolve_resolution(str(aspect))
        except KeyError as e:
            raise DispatchError("INVALID_ARGUMENT", str(e)) from None
    try:
        spec = RenderSpec(**payload)
    except Exception as e:
        raise DispatchError("INVALID_ARGUMENT", f"bad render spec: {e}") from None

    # 模板必须已注册：未知模板绝不静默回退（旧行为是全部渲成同一个画面）
    try:
        resolve_template(spec.template)
    except KeyError as e:
        raise DispatchError("INVALID_ARGUMENT", str(e)) from None

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
    async with content_job(
        repos,
        job_type="render_source",
        stage=JobStage.RENDERING,
        env=env,
        fail_code="RENDER_FAILED",
        lease="render_source",
        notify=deps.notify,
    ) as ctx:
        register(ctx.job.id, cancel_event)
        try:
            if spec.tts_engine.value == "user_audio":
                audio_uri = spec.user_audio_uri
            else:
                # Tranche 2：TTS 音频作为 artifact 保留（不再删除）
                audio_uri = await deps.tts.synthesize(
                    src.content, {"out_dir": tts_out_dir}
                )

            spec.caption_text = (src.content or "")[:200]

            # renderer.render 是阻塞的同步调用（跑 ffmpeg），放入 worker 线程，
            # 避免阻塞主事件循环。进度回调会跨线程触发，因此通过主线程的 loop
            # 把 DB 写入（transition）调度回主线程执行，确保所有 DB 访问留在
            # 创建连接的主线程（db_conn 使用 check_same_thread=True）。
            # 进度写去抖（T5）：每 ~5% 或 ≥1s 才提交一次 UPDATE，抑制写放大；
            # 每次落库同时 fire-and-forget 一条 job.progress 通知（Tranche 1）。
            loop = asyncio.get_running_loop()
            _last = {"progress": -1.0, "ts": 0.0}

            def _progress(prog: float) -> None:
                now = time.monotonic()
                if (prog - _last["progress"]) >= 0.05 or (now - _last["ts"]) >= 1.0:
                    captured = prog

                    def _commit(p: float) -> None:
                        updated = transition(
                            repos,
                            ctx.job.id,
                            JobState.RUNNING,
                            progress=p,
                            stage=JobStage.RENDERING,
                        )
                        # _commit 在主事件循环线程执行，可安全调度通知 task
                        emit_job_progress(deps.notify, updated)

                    loop.call_soon_threadsafe(_commit, captured)
                    _last["progress"] = prog
                    _last["ts"] = now

            result = await asyncio.to_thread(
                renderer.render, spec, audio_uri, _progress, cancel_event
            )

            # Tranche 2（PRD-REN-003）：.srt 字幕 sidecar（与 mp4 同目录，
            # 时长按 TTS 音频总时长等比分配）；失败降级为无字幕，不阻塞渲染
            video_path = result.video_uri.removeprefix("file://")
            audio_path = (audio_uri or "").removeprefix("file://")
            subtitles_path: str | None = None
            try:
                audio_duration = probe_audio_duration(audio_path)
                if audio_duration <= 0 and result.duration_seconds > 0:
                    audio_duration = result.duration_seconds
                subtitles_path = write_srt_sidecar(
                    video_path, src.content or "", audio_duration
                )
            except OSError:
                subtitles_path = None

            meta = VideoDraftMeta(
                video_uri=result.video_uri,
                duration_seconds=result.duration_seconds,
                template=result.template,
                tts_engine=result.tts_engine,
                resolution=spec.resolution,
                fps=spec.fps,
                source_version_id=spec.source_version_id,
                subtitles_uri=subtitles_path,
                audio_uri=audio_uri,
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
                notify=deps.notify,
            )
            # 费用透明（Tranche 2 / PRD-REN-002）：
            # - renderer：本机 ffmpeg，无外部费用 → estimated_cost=None
            # - tts：真正产生费用的环节，按**实际合成字符数**计价并单独审计。
            #   用户录音路径（user_audio）不调 TTS，故无 tts 调用记录。
            invocation = build_invocation(renderer, 0, model=result.template)
            record_provider_invocation(repos.conn, env, invocation)
            tts_invocation: dict[str, Any] | None = None
            if spec.tts_engine.value != "user_audio" and deps.tts is not None:
                tts_invocation = build_invocation(deps.tts, len(src.content or ""))
                record_provider_invocation(repos.conn, env, tts_invocation)
            return CommandResult(
                ok=True,
                commandId=env.commandId,
                job_id=ctx.job.id,
                artifact_ids=[cv_id],
                detail={
                    "video_uri": result.video_uri,
                    "template": result.template,
                    "tts_engine": result.tts_engine,
                    # Tranche 2（PRD-REN-001）：三产物绝对路径
                    "artifacts": {
                        "video": _abs_or_none(video_path),
                        "subtitles": _abs_or_none(subtitles_path),
                        "audio": _abs_or_none(audio_path),
                    },
                    "invocation": invocation,
                    # PRD-REN-002：旁白合成的来源与预计费用（user_audio 时为 None）
                    "tts_invocation": tts_invocation,
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
            # Tranche 2：TTS 音频作为 artifact 保留，不再在此清理
            clear(ctx.job.id)
