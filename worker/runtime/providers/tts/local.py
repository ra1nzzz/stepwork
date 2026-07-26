"""本地确定性 TTS（离线可跑，W6 / Tranche 3）。

按头脑风暴 P0：离线环境无真实 TTS API，本地实现生成**确定性 WAV**
（同文本 → 同哈希 → 同文件，可复用），使渲染管线在无密钥 / 无真实
引擎时也能端到端跑通"可运行"证伪。真实语音由 ``edge`` / ``cloud``
Provider 提供。

Tranche 3 修正：旧实现写 0 采样静音 WAV（时长恒为 0），
:func:`worker.runtime.render.subtitles.probe_audio_duration` 探得
时长 0，字幕退化为每句 2 秒兜底，与真实旁白时长脱节。现改为按文本
长度估算**真实时长**并写等长静音 PCM，使 probe → SRT 等比分配 →
渲染进度都拿到与文本相称的时长。仍是静音（离线不合成真实语音），
但时长真实、确定、可复现。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import wave
from typing import Any

# 估算旁白语速：中文朗读约 4~6 字/秒，取 5。opts["chars_per_sec"] 可覆盖。
_DEFAULT_CHARS_PER_SEC = 5.0
# 时长下限（空/极短文本也给一个可感知的最小时长）与上限（防病态超长文件）。
_MIN_DURATION_SEC = 0.5
_MAX_DURATION_SEC = 3600.0
# 静音 PCM 参数：16-bit mono 16kHz（wave 标准库可读，probe 走 nframes/rate）。
_SAMPLE_RATE = 16000
_SAMPLE_WIDTH = 2  # bytes（16-bit）
_CHANNELS = 1


def estimate_duration_sec(text: str, chars_per_sec: float) -> float:
    """按字符数与语速估算时长（秒），夹到 [下限, 上限]。"""
    cps = chars_per_sec if chars_per_sec > 0 else _DEFAULT_CHARS_PER_SEC
    raw = len(text or "") / cps
    return max(_MIN_DURATION_SEC, min(_MAX_DURATION_SEC, raw))


class LocalTTSProvider:
    """确定性本地 TTS（始终可用、零配置；静音但时长真实）。"""

    name = "local-tts"
    # 本地合成不产生费用（PRD-REN-002：明确为 0 而非「未知」）
    estimated_cost_per_1k = 0.0

    def __init__(
        self, out_dir: str | None = None, chars_per_sec: float = _DEFAULT_CHARS_PER_SEC
    ) -> None:
        self.out_dir = out_dir or os.path.join(
            tempfile.gettempdir(), "stepwork_tts"
        )
        self.chars_per_sec = chars_per_sec

    async def synthesize(
        self, text: str, opts: dict[str, Any] | None = None
    ) -> str:
        opts = opts or {}
        out_dir = opts.get("out_dir") or self.out_dir
        cps = float(opts.get("chars_per_sec") or self.chars_per_sec)
        os.makedirs(out_dir, exist_ok=True)

        duration = estimate_duration_sec(text, cps)
        # 文件名对（文本, 语速）确定：同输入 → 同文件，可复用缓存。
        # 前缀 v2 与旧 0 帧格式（tts_<digest>.wav）区隔，避免复用陈旧空文件。
        key = f"{text}\x00{cps:.6f}".encode()
        digest = hashlib.sha256(key).hexdigest()[:16]
        path = os.path.join(out_dir, f"tts_local_v2_{digest}.wav")
        await asyncio.to_thread(self._write_wav, path, duration)
        return "file://" + path

    def _write_wav(self, path: str, duration_sec: float) -> None:
        if os.path.exists(path):
            return
        n_frames = int(round(duration_sec * _SAMPLE_RATE))
        silence = b"\x00" * (_SAMPLE_WIDTH * _CHANNELS)
        # 先写临时文件再原子改名：并发/中断不会留下半截 WAV 被误当有效缓存。
        tmp = f"{path}.part"
        with wave.open(tmp, "wb") as w:
            w.setnchannels(_CHANNELS)
            w.setsampwidth(_SAMPLE_WIDTH)
            w.setframerate(_SAMPLE_RATE)
            w.writeframes(silence * n_frames)
        os.replace(tmp, path)
