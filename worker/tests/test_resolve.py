"""Provider 解析器测试（W3-W4 Batch3 集成）。

验证：
- resolve_asr 默认 auto（装了 whisper 用真实引擎、否则回退 local）、
  显式 local 强制 demo、resolve_ai 返回 None
- cloud 缺密钥回退 None
- per-request hint 能构造出正确的 provider 类型
"""

import os

import pytest

from worker.runtime.providers import resolve as resolve_mod
from worker.runtime.providers.ai.cloud import CloudAIProvider
from worker.runtime.providers.ai.openai_compatible import (
    OpenAICompatibleProvider,
)
from worker.runtime.providers.asr.local import LocalASRProvider
from worker.runtime.providers.asr.whisper import FasterWhisperASRProvider
from worker.runtime.providers.image.base import ImageProvider
from worker.runtime.providers.image.local import LocalImageProvider
from worker.runtime.providers.tts.edge import EdgeTTSProvider
from worker.runtime.providers.tts.local import LocalTTSProvider


def _clear_provider_env() -> None:
    for k in (
        "STEPWORK_ASR_PROVIDER",
        "STEPWORK_ASR_API_KEY",
        "STEPWORK_ASR_BASE_URL",
        "STEPWORK_ASR_MODEL",
        "STEPWORK_TTS_PROVIDER",
        "STEPWORK_TTS_API_KEY",
        "STEPWORK_TTS_BASE_URL",
        "STEPWORK_TTS_VOICE",
        "STEPWORK_AI_PROVIDER",
        "STEPWORK_AI_API_KEY",
        "STEPWORK_AI_BASE_URL",
        "STEPWORK_AI_MODEL",
        "STEPWORK_OPENAI_API_KEY",
        "STEPWORK_OPENAI_BASE_URL",
        "STEPWORK_OPENAI_MODEL",
    ):
        os.environ.pop(k, None)


def test_resolve_asr_explicit_local() -> None:
    _clear_provider_env()
    os.environ["STEPWORK_ASR_PROVIDER"] = "local"
    asr = resolve_mod.resolve_asr()
    assert isinstance(asr, LocalASRProvider)


def test_resolve_asr_auto_prefers_whisper_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 默认 auto：装了 faster-whisper → 真实引擎
    _clear_provider_env()
    monkeypatch.setattr(resolve_mod, "_has_module", lambda name: True)
    assert isinstance(resolve_mod.resolve_asr(), FasterWhisperASRProvider)


def test_resolve_asr_auto_falls_back_to_local_without_whisper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 默认 auto：未装 faster-whisper → 静默回退确定性 demo（非 None）
    _clear_provider_env()
    monkeypatch.setattr(
        resolve_mod, "_has_module", lambda name: name != "faster_whisper"
    )
    assert isinstance(resolve_mod.resolve_asr(), LocalASRProvider)


def test_resolve_asr_cloud_missing_keys_returns_none() -> None:
    _clear_provider_env()
    os.environ["STEPWORK_ASR_PROVIDER"] = "cloud"
    # 缺 key / url
    assert resolve_mod.resolve_asr() is None


def test_resolve_ai_no_env_returns_none() -> None:
    _clear_provider_env()
    assert resolve_mod.resolve_ai() is None


def test_resolve_ai_cloud_ok() -> None:
    _clear_provider_env()
    os.environ["STEPWORK_AI_PROVIDER"] = "cloud"
    os.environ["STEPWORK_AI_API_KEY"] = "k"
    os.environ["STEPWORK_AI_BASE_URL"] = "https://ai.example/v1"
    ai = resolve_mod.resolve_ai()
    assert isinstance(ai, CloudAIProvider)


def test_resolve_ai_ollama_ok_without_key() -> None:
    _clear_provider_env()
    os.environ["STEPWORK_AI_PROVIDER"] = "ollama"
    os.environ["STEPWORK_OPENAI_BASE_URL"] = "http://localhost:11434/v1"
    ai = resolve_mod.resolve_ai()
    assert isinstance(ai, OpenAICompatibleProvider)


def test_ai_provider_from_hint_cloud() -> None:
    _clear_provider_env()
    ai = resolve_mod.ai_provider_from_hint(
        {
            "kind": "cloud",
            "base_url": "https://ai.example/v1",
            "api_key": "k",
            "model": "m",
        }
    )
    assert isinstance(ai, CloudAIProvider)


def test_ai_provider_from_hint_ollama() -> None:
    _clear_provider_env()
    ai = resolve_mod.ai_provider_from_hint(
        {"kind": "ollama", "base_url": "http://localhost:11434/v1"}
    )
    assert isinstance(ai, OpenAICompatibleProvider)


def test_ai_provider_from_hint_missing_url_returns_none() -> None:
    _clear_provider_env()
    # cloud 但缺 base_url -> 无法构造
    assert (
        resolve_mod.ai_provider_from_hint({"kind": "cloud", "api_key": "k"}) is None
    )


def test_ai_provider_from_hint_empty_returns_none() -> None:
    _clear_provider_env()
    assert resolve_mod.ai_provider_from_hint(None) is None
    assert resolve_mod.ai_provider_from_hint({}) is None


# ----- Tranche 3：可选真实引擎（whisper / edge）import 守卫回退 -----


def test_resolve_default_tts_local() -> None:
    _clear_provider_env()
    assert isinstance(resolve_mod.resolve_tts(), LocalTTSProvider)


def test_resolve_asr_whisper_missing_package_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_provider_env()
    os.environ["STEPWORK_ASR_PROVIDER"] = "whisper"
    # 模拟未安装 faster-whisper：缺包必须回退 None（→ UNAVAILABLE），不崩溃
    monkeypatch.setattr(
        resolve_mod, "_has_module", lambda name: name != "faster_whisper"
    )
    assert resolve_mod.resolve_asr() is None


def test_resolve_asr_whisper_present_builds_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_provider_env()
    os.environ["STEPWORK_ASR_PROVIDER"] = "whisper"
    os.environ["STEPWORK_ASR_MODEL"] = "medium"
    # 模拟已安装：构造 provider（构造不触发引擎导入，模型加载惰性）
    monkeypatch.setattr(resolve_mod, "_has_module", lambda name: True)
    asr = resolve_mod.resolve_asr()
    assert isinstance(asr, FasterWhisperASRProvider)
    assert asr.model_size == "medium"


def test_resolve_tts_edge_missing_package_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_provider_env()
    os.environ["STEPWORK_TTS_PROVIDER"] = "edge"
    monkeypatch.setattr(
        resolve_mod, "_has_module", lambda name: name != "edge_tts"
    )
    assert resolve_mod.resolve_tts() is None


def test_resolve_tts_edge_present_builds_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_provider_env()
    os.environ["STEPWORK_TTS_PROVIDER"] = "edge"
    os.environ["STEPWORK_TTS_VOICE"] = "zh-CN-YunxiNeural"
    monkeypatch.setattr(resolve_mod, "_has_module", lambda name: True)
    tts = resolve_mod.resolve_tts()
    assert isinstance(tts, EdgeTTSProvider)
    assert tts.voice == "zh-CN-YunxiNeural"


# ----- 配图（S2：接口先于厂商实现）-----


def test_resolve_image_default_is_none() -> None:
    """厂商选型未定 → 默认不可用，绝不能静默落到占位图上。"""
    _clear_provider_env()
    assert resolve_mod.resolve_image() is None


def test_resolve_image_local_requires_explicit_opt_in() -> None:
    _clear_provider_env()
    os.environ["STEPWORK_IMAGE_PROVIDER"] = "local"
    image = resolve_mod.resolve_image()
    assert isinstance(image, LocalImageProvider)


def test_resolve_image_unknown_vendor_is_none() -> None:
    """未接的厂商名（含已下线的 stepfun）一律 None，不静默回落占位图。"""
    _clear_provider_env()
    for kind in ("stepfun", "wanxiang", "", "  "):
        os.environ["STEPWORK_IMAGE_PROVIDER"] = kind
        assert resolve_mod.resolve_image() is None, kind


def test_image_provider_from_hint() -> None:
    assert resolve_mod.image_provider_from_hint("local") is not None
    assert resolve_mod.image_provider_from_hint({"kind": "local"}) is not None
    assert resolve_mod.image_provider_from_hint(None) is None
    assert resolve_mod.image_provider_from_hint("") is None
    assert resolve_mod.image_provider_from_hint("nope") is None


def test_local_image_satisfies_protocol() -> None:
    """接口先于厂商的硬证据：实现只需满足协议，不必继承任何基类。"""
    assert isinstance(LocalImageProvider(), ImageProvider)
