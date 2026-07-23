"""从环境/请求提示构建 Provider 实例（W3-W4 Batch3 集成胶水）。

设计原则（三角色头脑风暴 P0：零硬编码密钥）：

- ASR/AI 的 base_url / api_key / model 仅来自 env 或显式传入的 config
- 任何 provider 缺失必要配置时返回 ``None``（handler 转译为
  ``UNAVAILABLE``），绝不把一个空密钥打到线上
- 支持 cloud / openai-compatible / ollama(=openai-compatible 本地)
  三种 AI 后端
- 支持 per-request provider 提示（``payload.provider``），使前端的
  provider-switch 真正生效，而非仅做 UI 展示
- 设置页保存的密钥进入**进程内存覆盖层**（``CONFIG_OVERRIDES``），
  按 ``workspace_id`` 隔离，绝不落 SQLite。
"""

from __future__ import annotations

import os
import threading
from typing import Any
from urllib.parse import urlparse

from worker.runtime.providers.ai.base import AIProvider
from worker.runtime.providers.ai.cloud import CloudAIProvider
from worker.runtime.providers.ai.openai_compatible import (
    OpenAICompatibleProvider,
)
from worker.runtime.providers.asr.base import ASRProvider
from worker.runtime.providers.asr.cloud import CloudASRProvider
from worker.runtime.providers.asr.local import LocalASRProvider
from worker.runtime.providers.renderer.base import RendererProvider
from worker.runtime.providers.renderer.ffmpeg import FFmpegRenderer
from worker.runtime.providers.tts.base import TTSProvider
from worker.runtime.providers.tts.cloud import CloudTTSProvider
from worker.runtime.providers.tts.local import LocalTTSProvider
from worker.runtime.render.ffmpeg_runner import FFmpegRunner


def _env(key: str) -> str | None:
    """读取环境变量，空串视为未设置。"""
    v = os.environ.get(key)
    return v if v else None


# ---------------------------------------------------------------------------
# 密钥覆盖层（SET.6 · 三角色 P0 安全模型）
#
# - 按 ``workspace_id`` 隔离，每个工作区持有自己的密钥子集。
# - 仅存在于**进程内存**，绝不写入 SQLite（与 Workspace.settings 分离）。
# - 按 section 深合并 + 全局锁：局部保存（如只改 LLM）不会清空
#   其它 section（ASR/TTS）或同 section 内未改动的密钥字段。
# - 空串 / 掩码占位符（``"••••"``）视为「未改动」，保留已存真实密钥，
#   彻底杜绝「重载后 store 回落空串、用户只改其它项保存即清空密钥」的问题。
# - dev_bridge 已用 ``_DB_LOCK`` 把请求串行化，这里再持 dict 级锁，
#   即便未来并发执行也安全。
# ---------------------------------------------------------------------------
CONFIG_OVERRIDES: dict[str, dict[str, Any]] = {}
CONFIG_LOCK = threading.Lock()

# 未改动占位符：前端回灌/保存时可能带上掩码值，需识别为「不覆盖」。
_MASK_PLACEHOLDER = "••••"


def apply_override(workspace_id: str, secrets: dict[str, Any]) -> None:
    """合并密钥子集到内存覆盖层（按 workspace_id 隔离，按 section 合并）。

    仅用 secrets 中**实际提供**的 section 覆盖旧值；空 dict 的 section
    保持不变。每个 section 内，仅用「非空且非掩码占位符」的值覆盖旧值，
    空串 / ``"••••"`` 保留已存的真实密钥。
    """
    with CONFIG_LOCK:
        current = CONFIG_OVERRIDES.get(workspace_id, {})
        merged: dict[str, Any] = dict(current)
        for section, section_cfg in secrets.items():
            if not isinstance(section_cfg, dict):
                continue
            existing = merged.get(section)
            existing = existing if isinstance(existing, dict) else {}
            new_section = dict(existing)
            for key, value in section_cfg.items():
                if value in ("", None, _MASK_PLACEHOLDER):
                    # 未改动 / 占位符：保留已存真实值（若有）
                    continue
                new_section[key] = value
            merged[section] = new_section
        CONFIG_OVERRIDES[workspace_id] = merged


def read_override(workspace_id: str) -> dict[str, Any]:
    """读取某工作区的密钥子集（返回副本，调用方放心使用）。"""
    with CONFIG_LOCK:
        return dict(CONFIG_OVERRIDES.get(workspace_id, {}))


def _override_for(workspace_id: str | None, section: str) -> dict[str, Any]:
    """取某工作区某 provider section 的密钥覆盖（无则空 dict）。"""
    if not workspace_id:
        return {}
    return read_override(workspace_id).get(section, {}) or {}


def _valid_base_url(url: str | None) -> bool:
    """基础校验 base_url：必须是 http/https，且能正常解析。

    本地单用户桌面场景下不做内网/IP 封锁（Ollama 常跑在 192.168.x）。
    暴露于非本机网络时才需进一步 SSRF 防护（见安全评审结论）。
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def resolve_asr(workspace_id: str | None = None) -> ASRProvider | None:
    """按 ``STEPWORK_ASR_PROVIDER`` 解析 ASR Provider。

    默认 ``local``（离线确定性，满足转写可运行证伪）；设为 ``cloud``
    时需要 ``STEPWORK_ASR_API_KEY`` + ``STEPWORK_ASR_BASE_URL``，否则
    回退 ``None``（handler 转译为 ``UNAVAILABLE``）。

    若 env 缺失，则回退到 ``workspace_id`` 对应的密钥覆盖层
    （来自设置页保存的密钥，仅存内存）。
    """
    kind = (_env("STEPWORK_ASR_PROVIDER") or "local").lower()
    if kind == "local":
        return LocalASRProvider()
    if kind == "cloud":
        ov = _override_for(workspace_id, "asr")
        key = _env("STEPWORK_ASR_API_KEY") or ov.get("apiKey")
        url = _env("STEPWORK_ASR_BASE_URL") or ov.get("baseUrl")
        if not key or not _valid_base_url(url):
            return None
        return CloudASRProvider(api_key=key, base_url=url)
    return None


def resolve_ai(workspace_id: str | None = None) -> AIProvider | None:
    """按 ``STEPWORK_AI_PROVIDER`` 解析 AI Provider。

    支持 ``cloud``（``STEPWORK_AI_*``）/ ``openai-compatible`` 或
    ``ollama``（``STEPWORK_OPENAI_*``，Ollama 通常无 key）。缺失必要
    配置返回 ``None``。env 缺失时回退到 ``workspace_id`` 的密钥覆盖层。
    """
    kind = (_env("STEPWORK_AI_PROVIDER") or "").lower()
    if not kind:
        return None
    if kind == "cloud":
        ov = _override_for(workspace_id, "llm")
        key = _env("STEPWORK_AI_API_KEY") or ov.get("apiKey")
        url = _env("STEPWORK_AI_BASE_URL") or ov.get("baseUrl")
        model = _env("STEPWORK_AI_MODEL") or ov.get("model")
        if not key or not _valid_base_url(url):
            return None
        return CloudAIProvider(api_key=key, base_url=url, model=model)
    if kind in ("openai-compatible", "openai_compatible", "ollama"):
        ov = _override_for(workspace_id, "llm")
        key = _env("STEPWORK_OPENAI_API_KEY") or ov.get("apiKey")
        url = _env("STEPWORK_OPENAI_BASE_URL") or ov.get("baseUrl")
        model = _env("STEPWORK_OPENAI_MODEL") or ov.get("model")
        if not _valid_base_url(url):
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


def resolve_tts(workspace_id: str | None = None) -> TTSProvider | None:
    """按 ``STEPWORK_TTS_PROVIDER`` 解析 TTS Provider（W6）。

    默认 ``local``（离线确定性占位，满足渲染可运行证伪）；
    设为 ``cloud`` 时需要 ``STEPWORK_TTS_API_KEY`` + ``STEPWORK_TTS_BASE_URL``，
    否则回退 ``None``（handler 转译为 ``UNAVAILABLE``）。
    env 缺失时回退到 ``workspace_id`` 的密钥覆盖层。
    """
    kind = (_env("STEPWORK_TTS_PROVIDER") or "local").lower()
    if kind == "local":
        return LocalTTSProvider()
    if kind == "cloud":
        ov = _override_for(workspace_id, "tts")
        key = _env("STEPWORK_TTS_API_KEY") or str(ov.get("apiKey") or "")
        url = _env("STEPWORK_TTS_BASE_URL") or str(ov.get("baseUrl") or "")
        model = _env("STEPWORK_TTS_MODEL") or str(ov.get("model") or "") or None
        if not key or not _valid_base_url(url):
            return None
        return CloudTTSProvider(api_key=key, base_url=url, model=model)
    return None


def resolve_renderer() -> RendererProvider | None:
    """W6 内置 FFmpeg 渲染器（vertical-caption-v1）。"""
    return FFmpegRenderer(FFmpegRunner())
