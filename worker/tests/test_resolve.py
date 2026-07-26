"""Provider 解析器测试（W3-W4 Batch3 集成）。

验证：
- env 缺失时 resolve_asr 默认 local、resolve_ai 返回 None
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


def test_resolve_asr_default_local() -> None:
    _clear_provider_env()
    asr = resolve_mod.resolve_asr()
    assert isinstance(asr, LocalASRProvider)


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
