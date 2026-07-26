"""TTS Provider 测试（W6）：本地确定性 + 云端（注入假 client）。"""

from __future__ import annotations

import asyncio
import os

from worker.runtime.providers.tts.local import LocalTTSProvider
from worker.runtime.render.subtitles import probe_audio_duration


def test_local_deterministic() -> None:
    provider = LocalTTSProvider()
    uri1 = asyncio.run(provider.synthesize("hello world"))
    uri2 = asyncio.run(provider.synthesize("hello world"))
    assert uri1 == uri2
    assert uri1.startswith("file://")
    assert os.path.exists(uri1.replace("file://", ""))
    # 空文本也应产出 wav，不抛错
    uri3 = asyncio.run(provider.synthesize(""))
    assert os.path.exists(uri3.replace("file://", ""))


def test_local_wav_has_real_duration() -> None:
    """Tranche 3：本地 WAV 时长真实（非 0 帧），probe 可探得且随文本增长。"""
    provider = LocalTTSProvider(chars_per_sec=5.0)

    short_uri = asyncio.run(provider.synthesize("十个字的一句话。"))
    short_path = short_uri.replace("file://", "")
    short_dur = probe_audio_duration(short_path)
    # 旧实现恒为 0；修复后必须 > 0，且不低于下限 0.5s
    assert short_dur >= 0.5

    long_text = "这是一段更长的旁白文本。" * 20
    long_uri = asyncio.run(provider.synthesize(long_text))
    long_dur = probe_audio_duration(long_uri.replace("file://", ""))
    # 更长文本 → 更长时长（时长与字符数正相关）
    assert long_dur > short_dur

    # 空文本落到时长下限（仍是合法非 0 帧 WAV）
    empty_dur = probe_audio_duration(
        asyncio.run(provider.synthesize("")).replace("file://", "")
    )
    assert empty_dur >= 0.5


class _FakeResp:
    content = b"FAKEAUDIO"

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *a: object) -> bool:
        return False

    async def post(
        self, url: str, headers: object = None, json: object = None,
        **kwargs: object,
    ) -> _FakeResp:
        return _FakeResp()


def test_cloud_with_injected_client() -> None:
    from worker.runtime.providers.tts.cloud import CloudTTSProvider

    provider = CloudTTSProvider(
        api_key="k", base_url="http://x", model="m", client=_FakeClient()
    )
    uri = asyncio.run(provider.synthesize("hi"))
    assert os.path.exists(uri.replace("file://", ""))
