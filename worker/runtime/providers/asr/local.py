"""本地确定性 ASR（W3，Batch 1）。

离线环境没有真实语音识别引擎。本实现以**确定性 fixture** 方式生成
可复现的草稿转写，满足 STRATEGY 中"≥18/20 媒体可转写"的"可运行"
证伪（三角色头脑风暴 P1：单测走 fixture，不伪造真实识别率）。

真实引擎接入时，只需替换 :meth:`transcribe` 的实现，对外契约不变。
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from worker.runtime.providers.asr.base import Transcript, TranscriptSegment

_DEMO_LINES = [
    "欢迎来到本期内容。",
    "今天我们聊聊自动化工作流。",
    "先把素材导进来，再做转写。",
    "转写完成之后，交给 AI 做内容分析。",
    "最后生成脚本并进入渲染。",
]


class LocalASRProvider:
    """确定性、无需外部依赖的本地转写 Provider。"""

    name = "local"

    def __init__(self, segment_sec: float = 4.0, max_chars: int = 20000) -> None:
        self.segment_sec = segment_sec
        self.max_chars = max_chars

    async def transcribe(
        self, media_uri: str, opts: dict[str, Any] | None = None
    ) -> Transcript:
        opts = opts or {}
        dur = float(opts.get("duration_sec") or 0.0)

        # 以 URI 哈希派生确定性起止偏移，保证同一文件结果可复现
        h = hashlib.sha256(media_uri.encode("utf-8")).hexdigest()
        offset = int(h[:8], 16) % len(_DEMO_LINES)
        lines = _DEMO_LINES[offset:] + _DEMO_LINES[:offset]

        if dur > 0:
            n_seg = max(1, math.ceil(dur / self.segment_sec))
        else:
            n_seg = len(lines)
        n_seg = min(n_seg, len(lines))

        segments = [
            TranscriptSegment(
                start=i * self.segment_sec,
                end=(i + 1) * self.segment_sec,
                text=lines[i],
            )
            for i in range(n_seg)
        ]
        text = "\n".join(s.text for s in segments)
        t = Transcript(
            text=text,
            language="zh",
            segments=segments,
            provider=self.name,
            duration_sec=dur or None,
        )
        return t.truncated(self.max_chars)
