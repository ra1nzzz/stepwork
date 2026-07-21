"""ASR（语音转写）Provider 子包（W3）。"""

from worker.runtime.providers.asr.base import (
    ASRProvider,
    Transcript,
    TranscriptSegment,
)
from worker.runtime.providers.asr.cloud import CloudASRProvider
from worker.runtime.providers.asr.local import LocalASRProvider

__all__ = [
    "ASRProvider",
    "Transcript",
    "TranscriptSegment",
    "LocalASRProvider",
    "CloudASRProvider",
]
