"""内置 FFmpeg 渲染器（vertical-caption-v1，W6）。

按头脑风暴 P0：FFmpeg 为受控外部二进制，参数用 **argv list**（不拼 shell）；
进度来自子进程 stderr 解析；取消时由 :mod:`worker.runtime.render.ffmpeg_runner`
终止并回收子进程（取消后 0 僵尸进程）。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from worker.runtime.models import RenderResult, RenderSpec
from worker.runtime.render.ffmpeg_runner import FFmpegRunner, FFmpegUnavailable


def _esc(text: str) -> str:
    """drawtext 过滤器的最小转义（冒号/反斜杠/单引号）。"""
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


class FFmpegRenderer:
    """基于 FFmpeg 的 9:16 字幕/背景模板渲染器。"""

    name = "ffmpeg-renderer"
    capability = "render:vertical-caption-v1"

    def __init__(
        self,
        runner: FFmpegRunner | None = None,
        ffmpeg_bin: str | None = None,
    ) -> None:
        self.runner = runner or FFmpegRunner()
        self.ffmpeg_bin = ffmpeg_bin

    def render(
        self,
        spec: RenderSpec,
        audio_uri: str,
        progress_cb: Callable[[float], None],
        cancel_event: Any,
    ) -> RenderResult:
        if not self.runner.available:
            raise FFmpegUnavailable()
        audio_path = audio_uri.replace("file://", "")
        out_dir = os.path.dirname(audio_path) or "."
        video_path = os.path.join(out_dir, f"draft_{spec.source_version_id}.mp4")
        w, h = spec.resolution
        caption = _esc((spec.caption_text or "STEPWORK")[:200])
        # argv list：绝不使用 shell 拼接（P0）
        args = [
            "-y",
            "-i", audio_path,
            "-f", "lavfi",
            "-i", f"color=c=navy:s={w}x{h}:r={spec.fps}",
            "-vf",
            (
                f"drawtext=text='{caption}':fontcolor=white:"
                f"fontsize=48:x=(w-text_w)/2:y=h-120"
            ),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            video_path,
        ]
        if self.ffmpeg_bin is not None:
            args = [self.ffmpeg_bin, *args]
        self.runner.run(args, progress_cb, cancel_event)
        return RenderResult(
            video_uri="file://" + video_path,
            duration_seconds=0.0,
            template=spec.template,
            tts_engine=spec.tts_engine.value
            if isinstance(spec.tts_engine, str)
            else str(spec.tts_engine),
        )
