"""从环境/请求提示构建 Provider 实例（W3-W4 Batch3 集成胶水）。

设计原则（三角色头脑风暴 P0：零硬编码密钥）：

- ASR/AI 的 base_url / api_key / model 仅来自 env 或显式传入的 config
- 任何 provider 缺失必要配置时返回 ``None``（handler 转译为
  ``UNAVAILABLE``），绝不把一个空密钥打到线上
- 支持 cloud / openai-compatible / ollama(=openai-compatible 本地)
  三种 AI 后端
- 支持 per-request provider 提示（``payload.provider``），使前端的
  provider-switch 真正生效，而非仅做 UI 展示
"""

from __future__ import annotations

import os
from typing import Any

from worker.runtime.providers.ai.base import AIProvider
from worker.runtime.providers.ai.cloud import CloudAIProvider
from worker.runtime.providers.ai.openai_compatible import (
    OpenAICompatibleProvider,
)
from worker.runtime.providers.asr.base import ASRProvider
from worker.runtime.providers.asr.cloud import CloudASRProvider
from worker.runtime.providers.asr.local import LocalASRProvider


def _env(key: str) -> str | None:
    """读取环境变量，空串视为未设置。"""
    v = os.environ.get(key)
    return v if v else None


def resolve_asr() -> ASRProvider | None:
    """按 ``STEPWORK_ASR_PROVIDER`` 解析 ASR Provider。

    默认 ``local``（离线确定性，满足转写可运行证伪）；设为 ``cloud``
    时需要 ``STEPWORK_ASR_API_KEY`` + ``STEPWORK_ASR_BASE_URL``，否则
    回退 ``None``（handler 转译为 ``UNAVAILABLE``）。
    """
    kind = (_env("STEPWORK_ASR_PROVIDER") or "local").lower()
    if kind == "local":
        return LocalASRProvider()
    if kind == "cloud":
        key = _env("STEPWORK_ASR_API_KEY")
        url = _env("STEPWORK_ASR_BASE_URL")
        if not key or not url:
            return None
        return CloudASRProvider(api_key=key, base_url=url)
    return None


def resolve_ai() -> AIProvider | None:
    """按 ``STEPWORK_AI_PROVIDER`` 解析 AI Provider。

    支持 ``cloud``（``STEPWORK_AI_*``）/ ``openai-compatible`` 或
    ``ollama``（``STEPWORK_OPENAI_*``，Ollama 通常无 key）。缺失必要
    配置返回 ``None``。
    """
    kind = (_env("STEPWORK_AI_PROVIDER") or "").lower()
    if not kind:
        return None
    if kind == "cloud":
        key = _env("STEPWORK_AI_API_KEY")
        url = _env("STEPWORK_AI_BASE_URL")
        model = _env("STEPWORK_AI_MODEL")
        if not key or not url:
            return None
        return CloudAIProvider(api_key=key, base_url=url, model=model)
    if kind in ("openai-compatible", "openai_compatible", "ollama"):
        key = _env("STEPWORK_OPENAI_API_KEY")
        url = _env("STEPWORK_OPENAI_BASE_URL")
        model = _env("STEPWORK_OPENAI_MODEL")
        if not url:
            return None
        return OpenAICompatibleProvider(api_key=key, base_url=url, model=model)
    return None


def ai_provider_from_hint(hint: dict[str, Any] | None) -> AIProvider | None:
    """从 per-request 提示（``payload.provider``）构建 AI Provider。

    前端 provider-switch 通过此钩子动态选择后端，使 UI 切换真正生效。
    提示字段：``kind``（cloud / openai-compatible / ollama）、
    ``base_url``、``api_key``、``model``。任一缺失则回退 ``None``
    （交由默认 provider）。
    """
    if not hint:
        return None
    kind = str(hint.get("kind", "")).lower()
    if kind in ("cloud",):
        url = hint.get("base_url") or None
        key = hint.get("api_key") or None
        model = hint.get("model") or None
        if not url or not key:
            return None
        return CloudAIProvider(api_key=key, base_url=url, model=model)
    if kind in ("openai-compatible", "openai_compatible", "ollama"):
        url = hint.get("base_url") or None
        key = hint.get("api_key") or None
        model = hint.get("model") or None
        if not url:
            return None
        return OpenAICompatibleProvider(api_key=key, base_url=url, model=model)
    return None
