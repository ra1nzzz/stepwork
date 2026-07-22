"""云端 AI Provider（W4，Batch 2）。

设计要点（三角色头脑风暴 P0）：
- **零硬编码密钥**：API Key / Base URL / Model 仅来自环境变量
  ``STEPWORK_AI_API_KEY`` / ``STEPWORK_AI_BASE_URL`` / ``STEPWORK_AI_MODEL``。
- 采用 OpenAI 兼容的 ``/chat/completions`` 请求体；当传入
  ``schema`` 时用 ``response_format=json_schema`` 约束结构化输出。
- 离线环境用 ``httpx`` 真实请求；测试注入 ``client`` 走 mock。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from worker.runtime.providers.ai.base import parse_json_response


def _env(key: str) -> str | None:
    v = os.environ.get(key)
    return v if v else None


class CloudAIProvider:
    """基于 HTTP 的云端 AI Provider（密钥仅来自 env）。"""

    name = "cloud-ai"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key or _env("STEPWORK_AI_API_KEY")
        self.base_url = (
            base_url or _env("STEPWORK_AI_BASE_URL")
            or "https://api.stepwork.local/ai/v1"
        )
        self.model = model or _env("STEPWORK_AI_MODEL") or "stepwork-default"
        cost = _env("STEPWORK_AI_COST_PER_1K")
        self.estimated_cost_per_1k = float(cost) if cost else 0.01
        self._client = client

    @asynccontextmanager
    async def _client_cm(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(timeout=120.0) as c:
            yield c

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("CloudAIProvider requires STEPWORK_AI_API_KEY")
        messages = [
            {
                "role": "system",
                "content": "你是内容分析助手，仅输出可被 JSON 解析的结构化结果。",
            },
            {"role": "user", "content": prompt},
        ]
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "analysis", "schema": schema},
            }
        async with self._client_cm() as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return parse_json_response(content)
