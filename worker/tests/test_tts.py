"""TTS Provider 测试（W6）：本地确定性 + 云端（注入假 client）。"""

from __future__ import annotations

import asyncio
import os

from worker.runtime.providers.tts.local import LocalTTSProvider


def test_local_deterministic() -> None:
    provider = LocalTTSProvider()
    uri1 = asyncio.run(provider.synthesize("hello world"))
    uri2 = asyncio.run(provider.synthesize("hello world"))
    assert uri1 == uri2
    assert uri1.startswith("file://")
    assert os.path.exists(uri1.replace("file://", ""))
    # 空文本也应产出静音 wav，不抛错
    uri3 = asyncio.run(provider.synthesize(""))
    assert os.path.exists(uri3.replace("file://", ""))


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
