"""faster-whisper ASR Provider（Tranche 3：可选真实语音识别）。

依赖**可选包** ``faster-whisper``（CTranslate2 后端的 Whisper，纯本地
离线推理）。未安装时由 :func:`worker.runtime.providers.resolve.resolve_asr`
提前返回 ``None``（handler 转 ``UNAVAILABLE``）；引擎导入与模型加载都
延迟到首次转写，故缺包不影响 provider 解析或 worker 启动。

模型权重首次使用时按 ``model_size`` 从 HuggingFace 下载并缓存到本地，
之后完全离线。CI 环境未安装 faster-whisper 且不下载模型，故此路径不在
自动化测试覆盖内（opt-in）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from worker.runtime.providers.asr.base import Transcript, TranscriptSegment

_DEFAULT_MODEL = "small"
_MAX_CHARS = 20000


class FasterWhisperASRProvider:
    """本地 Whisper 转写（可选；首次下载模型后离线可用）。"""

    name = "faster-whisper"

    def __init__(
        self,
        model_size: str = _DEFAULT_MODEL,
        device: str = "cpu",
        compute_type: str = "int8",
        max_chars: int = _MAX_CHARS,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.max_chars = max_chars
        self._model: Any = None

    def _load(self) -> Any:
        """惰性加载模型（重量级：进程内复用同一实例）。"""
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    async def transcribe(
        self, media_uri: str, opts: dict[str, Any] | None = None
    ) -> Transcript:
        opts = opts or {}
        path = media_uri[7:] if media_uri.startswith("file://") else media_uri
        # 阻塞的 CPU 密集推理放入线程，避免阻塞事件循环
        return await asyncio.to_thread(self._transcribe_sync, path, opts)

    def _transcribe_sync(self, path: str, opts: dict[str, Any]) -> Transcript:
        model = self._load()
        language = opts.get("language_hint") or None
        segments_iter, info = model.transcribe(path, language=language)

        segments: list[TranscriptSegment] = []
        texts: list[str] = []
        for s in segments_iter:
            piece = (s.text or "").strip()
            segments.append(
                TranscriptSegment(start=s.start, end=s.end, text=piece)
            )
            if piece:
                texts.append(piece)

        transcript = Transcript(
            text="\n".join(texts),
            language=getattr(info, "language", None),
            segments=segments,
            provider=self.name,
            duration_sec=getattr(info, "duration", None),
        )
        return transcript.truncated(self.max_chars)
