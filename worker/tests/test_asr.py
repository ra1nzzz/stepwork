"""Batch 1：ASR Provider（local 确定性 + cloud mock）测试。"""

from __future__ import annotations

import asyncio
import os

import httpx

from worker.runtime.providers.asr.base import TranscriptSegment
from worker.runtime.providers.asr.cloud import CloudASRProvider
from worker.runtime.providers.asr.local import LocalASRProvider


def test_local_deterministic() -> None:
    p = LocalASRProvider()
    t1 = asyncio.run(p.transcribe("file://a.mp4", {"duration_sec": 12}))
    t2 = asyncio.run(p.transcribe("file://a.mp4", {"duration_sec": 12}))
    assert t1.text == t2.text
    assert len(t1.segments) >= 1
    assert t1.provider == "local"


def test_local_no_duration_has_segments() -> None:
    p = LocalASRProvider()
    t = asyncio.run(p.transcribe("file://x.mp4"))
    assert len(t.segments) >= 1
    assert t.duration_sec is None


def test_local_truncates() -> None:
    p = LocalASRProvider(max_chars=5)
    t = asyncio.run(p.transcribe("file://a.mp4", {"duration_sec": 12}))
    assert len(t.text) <= 5


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": "云端转写结果。",
                "language": "zh",
                "segments": [{"start": 0.0, "end": 2.0, "text": "云端转写结果。"}],
                "duration_sec": 2.0,
            },
        )

    return httpx.MockTransport(handler)


def test_cloud_with_mock() -> None:
    client = httpx.AsyncClient(transport=_mock_transport())
    p = CloudASRProvider(api_key="test-key", client=client)
    t = asyncio.run(p.transcribe("file://a.mp4"))
    assert t.text == "云端转写结果。"
    assert t.language == "zh"
    assert len(t.segments) == 1
    assert isinstance(t.segments[0], TranscriptSegment)


def test_cloud_requires_key() -> None:
    os.environ.pop("STEPWORK_ASR_API_KEY", None)
    p = CloudASRProvider(api_key=None)
    try:
        asyncio.run(p.transcribe("file://a.mp4"))
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError when API key missing")
