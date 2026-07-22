"""OpenAI 兼容 / Ollama Provider（W4，Batch 2）。

``OpenAICompatibleProvider`` 是 :class:`CloudAIProvider` 的便捷子类，
仅替换默认凭证来源（独立的 ``STEPWORK_OPENAI_*`` 环境变量集），
请求/响应格式完全一致。

典型用法：
- 云端 OpenAI：``OpenAICompatibleProvider(api_key=...)``（默认指向 ``/v1``）
- 本地 Ollama：``OpenAICompatibleProvider(
  base_url="http://localhost:11434/v1", api_key=None)``
  （Ollama 不需要 API Key）
"""

from __future__ import annotations

import httpx

from worker.runtime.providers.ai.cloud import CloudAIProvider, _env


class OpenAICompatibleProvider(CloudAIProvider):
    """OpenAI 兼容端点（含本地 Ollama）Provider。"""

    name = "openai-compatible"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key or _env("STEPWORK_OPENAI_API_KEY"),
            base_url=base_url
            or _env("STEPWORK_OPENAI_BASE_URL")
            or "https://api.openai.com/v1",
            model=model or _env("STEPWORK_OPENAI_MODEL") or "gpt-4o-mini",
            client=client,
        )
