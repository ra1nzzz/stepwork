"""Batch 2：AI Provider（cloud / openai-compatible mock）测试。"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

from worker.runtime.providers.ai.base import parse_json_response
from worker.runtime.providers.ai.cloud import CloudAIProvider
from worker.runtime.providers.ai.openai_compatible import OpenAICompatibleProvider

_VALID_ANALYSIS: dict[str, Any] = {
    "summary": "本期聊自动化工作流。",
    "topics": ["自动化", "工作流"],
    "sentiment": "positive",
    "suggested_title": "自动化工作流入门",
    "suggested_tags": ["自动化", "效率"],
    "key_points": ["导入素材", "转写", "分析"],
    "target_audience": "内容创作者",
    "provider": "mock",
    "model": "mock-1",
    "confidence": 0.9,
}


def _mock_transport(payload: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"choices": [{"message": {"content": json.dumps(payload)}}]}
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def test_cloud_mock_returns_dict() -> None:
    client = httpx.AsyncClient(transport=_mock_transport(_VALID_ANALYSIS))
    p = CloudAIProvider(api_key="k", client=client)
    out = asyncio.run(p.complete("prompt", {"type": "object"}))
    assert out["summary"] == _VALID_ANALYSIS["summary"]
    assert out["sentiment"] == "positive"


def test_openai_compatible_inherits() -> None:
    client = httpx.AsyncClient(transport=_mock_transport(_VALID_ANALYSIS))
    p = OpenAICompatibleProvider(api_key="k", client=client)
    assert p.name == "openai-compatible"
    out = asyncio.run(p.complete("prompt"))
    assert out["model"] == "mock-1"


def test_cloud_requires_key() -> None:
    os.environ.pop("STEPWORK_AI_API_KEY", None)
    p = CloudAIProvider(api_key=None)
    try:
        asyncio.run(p.complete("prompt"))
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError when API key missing")


def test_parse_json_response_strips_fence() -> None:
    fenced = "```json\n" + '{"summary":"x","sentiment":"neutral"}' + "\n```"
    data = parse_json_response(fenced)
    assert data["summary"] == "x"
    assert data["sentiment"] == "neutral"
