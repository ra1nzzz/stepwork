"""``SynthesizeScenes`` 命令处理（S2 配音阶段）。

把「幕」变成**有时间轴的幕**：逐幕调 TTS → 实测音频时长 → 回写
``audio_uri`` / ``duration_sec`` / ``start_sec``，并（默认）把各幕音频拼成
一条整轨，供 ``CreateRenderJob`` 的 ``user_audio`` 路径直接消费。

**为什么必须有这一步**（此前流水线是断的，且断得很难发现）：

- 字幕 SRT 此前按**字符量等比**分配总时长（`render/subtitles.build_srt`）。
  真实语音语速并不均匀（数字、停顿、专有名词），等比分配必然与配音错位 ——
  这正是 S2 验收「字幕与配音对齐」过不了的根因。
- ``duration_sec`` 在 :class:`~worker.runtime.models.VideoScene` 的注释里被
  定为「TTS **实测**值，不是估值」：画面必须被音频时长驱动，反过来就是
  huashu-design 说的「失败模式 #1 = 带配音的 PPT」。

**落库纪律**：时间轴用 ``apply_timeline`` **单事务**写。逐幕更新会在中途留下
半新半旧的时间轴，渲染器照着它出片就是字幕错位。

**拼接是尽力而为**：整轨只是「让渲染器今天就能吃」的 convenience，真正的
事实是每幕的实测时长。ffmpeg 不在时不应让整条命令失败 —— 但也不会静默，
失败会写进 ``detail.concatError`` 并把 ``audioUri`` 置 ``null``。
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import content_job, finish_job
from worker.runtime.models import CommandEnvelope, CommandResult, JobStage
from worker.runtime.providers.resolve import ffmpeg_runner
from worker.runtime.render.ffmpeg_runner import FFmpegRunner
from worker.runtime.render.subtitles import probe_audio_duration

#: 拼接产物格式（整轨给渲染器用，AAC 通用且渲染侧已验证）
_CONCAT_SUFFIX = ".m4a"


def _local_path(uri: str) -> str:
    """``file://`` uri → 本地路径（其它 scheme 原样返回）。"""
    return uri.removeprefix("file://") if uri.startswith("file://") else uri


def _measure_duration(audio_uri: str, runner: FFmpegRunner | None) -> float:
    """实测音频时长（秒）；探不到返回 ``0.0``。

    两级探测，缺一不可：

    - :func:`probe_audio_duration`：WAV 走标准库，**零依赖**，本地 TTS 与
      大多数离线 provider 都是 WAV；
    - ``FFmpegRunner.probe``：真实 provider 出 mp3/m4a，标准库读不了，
      必须 ffprobe/ffmpeg 兜底。
    """
    path = _local_path(audio_uri)
    duration = probe_audio_duration(path)
    if duration > 0:
        return duration
    if runner is not None and runner.available:
        try:
            return float(runner.probe(path))
        except Exception:  # noqa: BLE001 - 探测失败按 0 处理，由调用方判定
            return 0.0
    return 0.0


def _write_concat_list(paths: list[str], list_path: Path) -> None:
    """写 ffmpeg concat demuxer 清单（Windows 反斜杠必须转正斜杠）。"""
    lines = []
    for p in paths:
        posix = str(Path(p).resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{posix}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _concat_audio(
    audio_uris: list[str], out_path: str, runner: FFmpegRunner
) -> None:
    """把各幕音频拼成一条整轨（concat demuxer + 重编码 AAC）。

    ``-c:a aac`` 而非 ``-c copy``：不同幕可能来自不同 provider / 采样率，
    copy 在参数不一致时会静默产出截断音频。重编码慢一点但结果确定。
    """
    list_path = Path(out_path).with_suffix(".concat.txt")
    _write_concat_list([_local_path(u) for u in audio_uris], list_path)
    try:
        runner.run(
            [
                "-y",
                "-f", "concat", "-safe", "0", "-i", str(list_path),
                "-c:a", "aac", "-b:a", "192k",
                out_path,
            ],
            lambda _p: None,
            threading.Event(),
            timeout_sec=600,
        )
    finally:
        list_path.unlink(missing_ok=True)


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``SynthesizeScenes``。"""
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
        # 没幕就没法配音。给明确错误而不是空转成功——空成功会让调用方
        # 以为时间轴已经就绪，渲染时才发现全 0。
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"version {version_id} has no scenes (run GenerateScript or "
            "SaveVideoScenes first)",
        )

    tts = deps.tts
    if tts is None:
        raise DispatchError("UNAVAILABLE", "tts provider not configured")

    out_dir = str(payload.get("outDir") or os.path.join(
        tempfile.gettempdir(), "stepwork_tts"
    ))
    want_concat = bool(payload.get("concat", True))
    # 复用渲染器已解析好的 ffmpeg 路径（同一份二进制配置），拿不到就按
    # STEPWORK_FFMPEG_BIN / PATH 解析 —— 不能直接 FFmpegRunner()，
    # 那只查 PATH，WinGet 装的 ffmpeg 会「明明在却报不可用」。
    base_runner = getattr(deps.renderer, "runner", None)
    runner = base_runner if isinstance(base_runner, FFmpegRunner) else ffmpeg_runner()

    async with content_job(
        repos,
        job_type="synthesize_scenes",
        stage=JobStage.SYNTHESIZING,
        env=env,
        fail_code="TTS_FAILED",
        notify=deps.notify,
    ) as ctx:
        cursor = 0.0
        skipped: list[int] = []
        total = len(scenes)
        for i, scene in enumerate(scenes):
            text = (scene.text or "").strip()
            if not text:
                # 空幕不配音：合成静音只会凭空多出一段无意义的时长，
                # 把后面所有幕的 start_sec 一起推歪
                scene.audio_uri = None
                scene.duration_sec = 0.0
                scene.start_sec = cursor
                skipped.append(scene.seq)
                continue
            try:
                audio_uri = await tts.synthesize(
                    text, {"out_dir": out_dir, "emotion": scene.emotion}
                )
            except DispatchError:
                raise
            except Exception as e:  # noqa: BLE001 - provider 异常统一转译
                raise DispatchError(
                    "TTS_FAILED", f"scene seq={scene.seq} synth failed: {e}"
                ) from None
            duration = _measure_duration(audio_uri, runner)
            if duration <= 0:
                # 实测不到时长就落库 = 时间轴里埋了一个 0，渲染必然错位。
                # 宁可整条失败，让调用方看见。
                raise DispatchError(
                    "TTS_FAILED",
                    f"scene seq={scene.seq} audio duration could not be measured "
                    f"({audio_uri})",
                )
            scene.audio_uri = audio_uri
            scene.duration_sec = duration
            scene.start_sec = cursor
            cursor += duration
            ctx.progress(0.05 + 0.85 * ((i + 1) / total), JobStage.SYNTHESIZING)

        # 时间轴一次性落库（半新半旧比全旧更危险）
        repos.video_scenes.apply_timeline(str(version_id), scenes)

        concat_uri: str | None = None
        concat_error: str | None = None
        if want_concat:
            voiced = [s.audio_uri for s in scenes if s.audio_uri]
            if not voiced:
                concat_error = "no voiced scenes to concat"
            elif not runner.available:
                concat_error = "ffmpeg not available"
            else:
                out_path = os.path.join(out_dir, f"scenes_{version_id}{_CONCAT_SUFFIX}")
                try:
                    _concat_audio(voiced, out_path, runner)
                    concat_uri = "file://" + out_path
                except Exception as e:  # noqa: BLE001 - 尽力而为，见模块 docstring
                    concat_error = f"{type(e).__name__}: {e}"

        # 配音不新建 content_version，用不了 persist_content_version；
        # 但 content_job 的成功路径不会自动置 SUCCEEDED —— 不显式收尾的话
        # 这个 job 会永远停在 RUNNING（会被过期扫描误判成待重试）。
        finish_job(
            repos,
            ctx.job,
            stage=JobStage.SYNTHESIZING,
            artifact_ids=[s.audio_uri for s in scenes if s.audio_uri],
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
            "count": len(scenes),
            "totalDurationSec": round(cursor, 3),
            # 整轨：可直接喂给 CreateRenderJob 的 user_audio 路径
            "audioUri": concat_uri,
            "concatError": concat_error,
            "skippedSeqs": skipped,
            "scenes": [
                {
                    "id": s.id,
                    "seq": s.seq,
                    "startSec": round(s.start_sec, 3),
                    "durationSec": round(s.duration_sec, 3),
                    "audioUri": s.audio_uri,
                }
                for s in scenes
            ],
        },
    )
