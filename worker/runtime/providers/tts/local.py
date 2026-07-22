"""本地确定性 TTS（离线可跑占位，W6）。

按头脑风暴 P0：离线环境无真实 TTS API，本地实现生成**确定性静音
WAV 占位**（同文本 → 同哈希 → 同文件，可复用），使渲染管线在
无 ffmpeg / 无密钥时也能端到端跑通"可运行"证伪。真实语音属
云端 Provider 范畴。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import struct
import tempfile
from typing import Any


def _silent_wav() -> bytes:
    """构造一个合法的最小静音 WAV（mono / 8000Hz / 8-bit PCM）。"""
    data = b""
    riff = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    fmt = b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8
    )
    wav_data = b"data" + struct.pack("<I", len(data))
    return riff + fmt + wav_data


class LocalTTSProvider:
    """确定性本地占位 TTS（始终可用、零配置）。"""

    name = "local-tts"

    def __init__(self, out_dir: str | None = None) -> None:
        self.out_dir = out_dir or os.path.join(
            tempfile.gettempdir(), "stepwork_tts"
        )

    async def synthesize(
        self, text: str, opts: dict[str, Any] | None = None
    ) -> str:
        out_dir = (opts or {}).get("out_dir") or self.out_dir
        os.makedirs(out_dir, exist_ok=True)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        path = os.path.join(out_dir, f"tts_{digest}.wav")
        await asyncio.to_thread(self._write_wav, path)
        return "file://" + path

    def _write_wav(self, path: str) -> None:
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(_silent_wav())
