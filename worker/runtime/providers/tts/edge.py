"""EdgeTTS Provider（Tranche 3：可选真实语音）。

依赖**可选包** ``edge-tts``（微软在线神经语音）。未安装时由
:func:`worker.runtime.providers.resolve.resolve_tts` 提前返回 ``None``
（handler 转 ``UNAVAILABLE``），本模块的引擎导入延迟到 ``synthesize``
内部，故缺包也不会拖垮 provider 解析或 worker 启动。

注意：edge-tts 走微软**在线**服务，并非纯离线，属"本地优先但可选
联网"能力，须用户显式 ``STEPWORK_TTS_PROVIDER=edge`` 才启用。输出为
MP3；:func:`probe_audio_duration` 目前仅解析 WAV，故字幕时长对 edge
音频走兜底分配（后续可接 mp3 时长探测再改进）。

CI 环境未安装 edge-tts，故此路径不在自动化测试覆盖内（opt-in）。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from typing import Any

# 默认中文女声；opts["voice"] / STEPWORK_TTS_VOICE 可覆盖。
_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


class EdgeTTSProvider:
    """微软 Edge 神经语音 TTS（可选、需联网）。"""

    name = "edge-tts"

    def __init__(self, voice: str | None = None, out_dir: str | None = None) -> None:
        self.voice = voice or _DEFAULT_VOICE
        self.out_dir = out_dir or os.path.join(
            tempfile.gettempdir(), "stepwork_tts"
        )

    async def synthesize(
        self, text: str, opts: dict[str, Any] | None = None
    ) -> str:
        opts = opts or {}
        # 延迟导入：缺包时由 resolve 侧提前拦截，此处不会被走到
        import edge_tts

        out_dir = opts.get("out_dir") or self.out_dir
        voice = opts.get("voice") or self.voice
        os.makedirs(out_dir, exist_ok=True)

        # 对（voice, text）确定：同输入 → 同文件，可复用缓存
        digest = hashlib.sha256(
            f"{voice}\x00{text}".encode()
        ).hexdigest()[:16]
        path = os.path.join(out_dir, f"tts_edge_{digest}.mp3")
        # 文件系统探测放线程，避免阻塞事件循环（ASYNC240）
        if not await asyncio.to_thread(os.path.exists, path):
            # edge-tts 对空文本会报错：兜底一个空格
            communicate = edge_tts.Communicate(text or " ", voice)
            tmp = f"{path}.part"
            await communicate.save(tmp)
            await asyncio.to_thread(os.replace, tmp, path)
        return "file://" + path
