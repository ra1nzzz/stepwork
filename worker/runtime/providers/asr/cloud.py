"""云端 ASR Provider（W3，Batch 1）。

设计要点（三角色头脑风暴 P0）：
- **零硬编码密钥**：API Key / Base URL 仅来自环境变量
  ``STEPWORK_ASR_API_KEY`` / ``STEPWORK_ASR_BASE_URL``。
- 离线环境用 ``httpx`` 发起真实请求；测试注入 ``client`` 走 mock，
  不依赖网络。
- 密钥缺失时不发起请求，立即抛错，避免把空密钥打到线上。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from worker.runtime.providers.asr.base import Transcript, TranscriptSegment


def _env(key: str) -> str | None:
    v = os.environ.get(key)
    return v if v else None


class CloudASRProvider:
    """基于 HTTP 的云端转写 Provider（密钥仅来自 env）。"""

    name = "cloud"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key or _env("STEPWORK_ASR_API_KEY")
        self.base_url = (
            base_url or _env("STEPWORK_ASR_BASE_URL")
            or "https://api.stepwork.local/asr"
        )
        self._client = client

    @asynccontextmanager
    async def _client_cm(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(timeout=60.0) as c:
            yield c

    async def transcribe(
        self, media_uri: str, opts: dict[str, Any] | None = None
    ) -> Transcript:
        if not self.api_key:
            raise RuntimeError("CloudASRProvider requires STEPWORK_ASR_API_KEY")
        opts = opts or {}
        async with self._client_cm() as client:
            resp = await client.post(
                f"{self.base_url}/transcribe",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"media_uri": media_uri, "opts": opts},
            )
            resp.raise_for_status()
            data = resp.json()
        segments = [TranscriptSegment(**s) for s in data.get("segments", [])]
        return Transcript(
            text=data.get("text", ""),
            language=data.get("language"),
            segments=segments,
            provider=self.name,
            duration_sec=data.get("duration_sec"),
        )
