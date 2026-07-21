"""ASR Provider 协议与公共类型（W3，Batch 1）。

``ASRProvider`` 为结构化协议（PEP 544，``runtime_checkable``）。
所有实现（local / cloud）只需满足 ``name`` + ``transcribe`` 签名即可，
无需显式继承。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class TranscriptSegment(BaseModel):
    """单条转写片段（时间戳 + 文本）。"""

    start: float = 0.0
    end: float = 0.0
    text: str = ""


class Transcript(BaseModel):
    """一次转写的完整结果。"""

    text: str = ""
    language: str | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
    provider: str = ""
    duration_sec: float | None = None

    def truncated(self, max_chars: int) -> Transcript:
        """超过 ``max_chars`` 时截断 ``text``（落库前的字符上限保护）。"""
        if max_chars <= 0 or len(self.text) <= max_chars:
            return self
        return Transcript(
            text=self.text[:max_chars],
            language=self.language,
            segments=self.segments,
            provider=self.provider,
            duration_sec=self.duration_sec,
        )


@runtime_checkable
class ASRProvider(Protocol):
    """语音转写 Provider 协议。"""

    name: str

    async def transcribe(
        self, media_uri: str, opts: dict[str, Any] | None = None
    ) -> Transcript:
        """转写 ``media_uri`` 指向的媒体。

        Args:
            media_uri: 本地媒体文件路径（``file://`` 或绝对路径）。
            opts: 可选参数（如 ``duration_sec`` / ``language_hint``）。

        Returns:
            :class:`Transcript`。
        """
        ...
