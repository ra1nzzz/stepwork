"""StepFun 复刻音色 TTS Provider 测试（S2 配音：从静音 WAV 到真实人声）。

锁死四件事：

1. **端点与请求体形状**：复刻音色走 ``/step_plan/v1/audio/speech``，字段是
   ``input``（不是 ``text``）+ ``voice`` + ``instruction``。写错字段名不会
   报错，只会得到一段「读什么都一样」的默认音 —— 最难查的那类 bug；
2. **命中错误缓存必须炸**：不同文本得到同一段音频（MD5 相同）= 接口返回了
   固定错误句，静默渲进成片是灾难。判据用 MD5 而非字节数（CBR 会撞车误报）；
3. **错误信息带 body 片段 + 状态码提示**：402 是余额、404 可能是端点写错，
   也可能是**模型在 PLAN 端点无权**（body 带 ``model_invalid``）—— 后者得先
   认出来，否则会把排查方向引到路径上，只报 HTTP 状态码等于让人猜；
4. **幂等与归一化**：同（音色, 指令, 语速, 文本）复用文件不重复计费；
   语速被 ``instruction`` 带跑时按 字/秒 用 atempo 拉回（不变调）。

同时覆盖 :func:`resolve_tts` 的 ``stepfun`` 接线。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from worker.runtime.providers.resolve import resolve_tts
from worker.runtime.providers.tts.base import TTSError, TTSProvider
from worker.runtime.providers.tts.stepfun import (
    StepFunTTSProvider,
    atempo_filter,
    count_voiced_chars,
)

_MP3 = b"ID3\x03\x00\x00\x00fake-audio-bytes"


class _FakeResponse:
    """最小 httpx.Response 替身。"""

    def __init__(
        self, status_code: int = 200, content: bytes = b"", text: str = ""
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.text = text


class _FakeClient:
    """最小 httpx.AsyncClient 替身；按顺序回放响应，记录请求体。"""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class _FakeRunner:
    """最小 FFmpegRunner 替身：记录 probe / run，不真跑 ffmpeg。"""

    def __init__(self, duration: float = 0.0, available: bool = True) -> None:
        self.available = available
        self.duration = duration
        self.run_args: list[list[str]] = []

    def probe(self, path: str) -> float:
        return self.duration

    def run(
        self,
        args: list[str],
        progress_cb: Any,
        cancel_event: Any,
        timeout_sec: int = 0,
    ) -> int:
        self.run_args.append(args)
        return 0


def _read(uri: str) -> bytes:
    """同步读文件：async 用例里不能用 ``Path.read_bytes``（ruff ASYNC240）。"""
    return Path(uri.removeprefix("file://")).read_bytes()


def _provider(client: _FakeClient, **kwargs: Any) -> StepFunTTSProvider:
    kwargs.setdefault("voice", "voice-tone-TEST")
    # 关掉归一化：多数用例只想验请求/落盘，语速那几条单独开
    kwargs.setdefault("normalize_speed", False)
    return StepFunTTSProvider(api_key="sk-test", client=client, **kwargs)


# ---------------------------------------------------------------------------
# 请求形状


async def test_endpoint_and_body_shape(tmp_path: Path) -> None:
    client = _FakeClient([_FakeResponse(content=_MP3)])
    p = _provider(client)
    uri = await p.synthesize("今天聊聊性别对立。", {"out_dir": str(tmp_path)})

    assert uri.startswith("file://")
    assert _read(uri) == _MP3
    assert client.calls[0]["url"] == "https://api.stepfun.com/step_plan/v1/audio/speech"
    assert client.calls[0]["headers"]["Authorization"] == "Bearer sk-test"
    body = client.calls[0]["json"]
    assert body["model"] == "stepaudio-2.5-tts"
    assert body["input"] == "今天聊聊性别对立。"
    assert body["voice"] == "voice-tone-TEST"
    # 没给 emotion 就不要塞 instruction（空串会让服务端拿到空指令）
    assert "instruction" not in body


async def test_emotion_maps_to_instruction(tmp_path: Path) -> None:
    client = _FakeClient([_FakeResponse(content=_MP3)])
    p = _provider(client)
    await p.synthesize("一句话。", {"out_dir": str(tmp_path), "emotion": "沉稳地讲述"})
    assert client.calls[0]["json"]["instruction"] == "沉稳地讲述"


async def test_opts_voice_and_speed_override(tmp_path: Path) -> None:
    client = _FakeClient([_FakeResponse(content=_MP3)])
    p = _provider(client)
    await p.synthesize(
        "一句话。", {"out_dir": str(tmp_path), "voice": "voice-tone-OTHER", "speed": 1.0}
    )
    body = client.calls[0]["json"]
    assert body["voice"] == "voice-tone-OTHER"
    assert body["speed"] == 1.0


async def test_bad_speed_falls_back_to_provider_default(tmp_path: Path) -> None:
    client = _FakeClient([_FakeResponse(content=_MP3)])
    p = _provider(client)
    await p.synthesize("一句话。", {"out_dir": str(tmp_path), "speed": "快一点"})
    assert client.calls[0]["json"]["speed"] == 1.25


# ---------------------------------------------------------------------------
# 缓存与幂等


async def test_same_input_reuses_file_without_reposting(tmp_path: Path) -> None:
    client = _FakeClient([_FakeResponse(content=_MP3)])
    p = _provider(client)
    first = await p.synthesize("同一句话。", {"out_dir": str(tmp_path)})
    second = await p.synthesize("同一句话。", {"out_dir": str(tmp_path)})
    assert first == second
    assert len(client.calls) == 1


async def test_different_emotion_is_a_different_file(tmp_path: Path) -> None:
    client = _FakeClient([_FakeResponse(content=_MP3), _FakeResponse(content=_MP3)])
    p = _provider(client)
    a = await p.synthesize("同一句话。", {"out_dir": str(tmp_path), "emotion": "平静"})
    b = await p.synthesize("同一句话。", {"out_dir": str(tmp_path), "emotion": "激动"})
    assert a != b
    assert len(client.calls) == 2


# ---------------------------------------------------------------------------
# 错误缓存检测（本 provider 存在的首要理由）


async def test_identical_audio_for_different_text_raises(tmp_path: Path) -> None:
    client = _FakeClient([_FakeResponse(content=_MP3)])
    p = _provider(client)
    await p.synthesize("第一句话。", {"out_dir": str(tmp_path)})
    with pytest.raises(TTSError, match="错误缓存"):
        await p.synthesize("完全不同的另一句话。", {"out_dir": str(tmp_path)})


async def test_distinct_audio_passes(tmp_path: Path) -> None:
    client = _FakeClient(
        [_FakeResponse(content=_MP3), _FakeResponse(content=_MP3 + b"-2")]
    )
    p = _provider(client)
    await p.synthesize("第一句话。", {"out_dir": str(tmp_path)})
    await p.synthesize("第二句话。", {"out_dir": str(tmp_path)})
    assert len(client.calls) == 2


# ---------------------------------------------------------------------------
# 错误可读性


@pytest.mark.parametrize(
    ("status", "hint"),
    [(401, "密钥无效"), (402, "余额"), (404, "/step_plan/"), (429, "限流")],
)
async def test_http_errors_carry_hint_and_body(
    tmp_path: Path, status: int, hint: str
) -> None:
    client = _FakeClient([_FakeResponse(status_code=status, text='{"error":"boom"}')])
    p = _provider(client, max_attempts=1)
    with pytest.raises(TTSError) as ei:
        await p.synthesize("一句话。", {"out_dir": str(tmp_path)})
    msg = str(ei.value)
    assert str(status) in msg
    assert hint in msg
    assert "boom" in msg


_MODEL_INVALID_BODY = (
    '{"error":{"message":"The model \\"step-tts-2\\" does not exist or you do not '
    'have access to it.","type":"model_invalid","code":"model_invalid"}}'
)


async def test_model_invalid_is_named_as_model_problem(tmp_path: Path) -> None:
    """模型不被 PLAN 端点接受时必须是「模型不可用」，不能报成「端点不存在」。

    真机实测（2026-09-13）：``step-tts-2`` 返回 **404 + model_invalid**。
    若只按状态码翻，用户会去查 ``/step_plan/`` 路径 —— 而路径是对的。
    """
    client = _FakeClient([_FakeResponse(status_code=404, text=_MODEL_INVALID_BODY)])
    p = _provider(client, max_attempts=1, model="step-tts-2")
    with pytest.raises(TTSError) as ei:
        await p.synthesize("一句话。", {"out_dir": str(tmp_path)})
    msg = str(ei.value)
    assert "模型不可用" in msg
    assert "step-tts-2" in msg, "要报出**当前配错的那个**模型名，否则不知道改什么"
    assert "stepaudio-2.5-tts" in msg, "要报出正确的模型名"
    assert "端点不存在" not in msg, "端点是对的，不能把人引去查路径"


async def test_empty_audio_raises(tmp_path: Path) -> None:
    client = _FakeClient([_FakeResponse(content=b"")])
    p = _provider(client, max_attempts=1)
    with pytest.raises(TTSError, match="空音频"):
        await p.synthesize("一句话。", {"out_dir": str(tmp_path)})


async def test_5xx_is_retried_then_succeeds(tmp_path: Path) -> None:
    client = _FakeClient(
        [
            _FakeResponse(status_code=500, text="upstream boom"),
            _FakeResponse(status_code=500, text="upstream boom"),
            _FakeResponse(content=_MP3),
        ]
    )
    p = _provider(client, max_attempts=3)
    uri = await p.synthesize("一句话。", {"out_dir": str(tmp_path)})
    assert uri.startswith("file://")
    assert len(client.calls) == 3


async def test_4xx_is_not_retried(tmp_path: Path) -> None:
    client = _FakeClient([_FakeResponse(status_code=400, text="bad voice")])
    p = _provider(client, max_attempts=3)
    with pytest.raises(TTSError, match="bad voice"):
        await p.synthesize("一句话。", {"out_dir": str(tmp_path)})
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# 语速归一化


def test_count_voiced_chars_ignores_punctuation() -> None:
    # 标点不占发音时长，按总字符算语速会把标点多的幕误判成超速
    assert count_voiced_chars("你好，世界！") == 4
    assert count_voiced_chars("AI 时代") == 4


def test_atempo_filter_chains_beyond_range() -> None:
    # atempo 单次只吃 0.5–2.0，超出必须串联（单次传 2.5 会被静默截断）
    assert atempo_filter(1.25) == "atempo=1.2500"
    chained = atempo_filter(2.5)
    assert chained.count("atempo=") == 2


async def test_slow_audio_is_sped_up(tmp_path: Path) -> None:
    client = _FakeClient([_FakeResponse(content=_MP3)])
    runner = _FakeRunner(duration=10.0)  # 30 字 / 10s = 3.0 字/秒，明显偏慢
    p = _provider(client, normalize_speed=True, runner=runner)
    await p.synthesize("一二三四五六七八九十" * 3, {"out_dir": str(tmp_path)})
    af = runner.run_args[0][runner.run_args[0].index("-af") + 1]
    assert af.startswith("atempo=")
    assert float(af.removeprefix("atempo=").split(",")[0]) > 1.0


async def test_normal_rate_is_left_alone(tmp_path: Path) -> None:
    client = _FakeClient([_FakeResponse(content=_MP3)])
    runner = _FakeRunner(duration=5.0)  # 30 字 / 5s = 6.0 字/秒，正好
    p = _provider(client, normalize_speed=True, runner=runner)
    await p.synthesize("一二三四五六七八九十" * 3, {"out_dir": str(tmp_path)})
    assert runner.run_args == []


async def test_missing_ffmpeg_skips_normalization(tmp_path: Path) -> None:
    """ffmpeg 不在时不归一化，但配音本身必须照常出（不能因为增强失败而失败）。"""
    client = _FakeClient([_FakeResponse(content=_MP3)])
    p = _provider(
        client, normalize_speed=True, runner=_FakeRunner(available=False, duration=9.0)
    )
    uri = await p.synthesize("一二三四五六七八九十" * 3, {"out_dir": str(tmp_path)})
    assert _read(uri) == _MP3


# ---------------------------------------------------------------------------
# 协议与接线


async def test_satisfies_tts_protocol(tmp_path: Path) -> None:
    p = _provider(_FakeClient([_FakeResponse(content=_MP3)]))
    assert isinstance(p, TTSProvider)


def test_resolve_stepfun_needs_key_and_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEPWORK_TTS_PROVIDER", "stepfun")
    monkeypatch.delenv("STEPWORK_TTS_API_KEY", raising=False)
    monkeypatch.delenv("STEPWORK_TTS_VOICE", raising=False)
    assert resolve_tts() is None

    monkeypatch.setenv("STEPWORK_TTS_API_KEY", "sk-test")
    # 只有 key 没有音色 id：拿默认音色出片等于「换了个主播」，宁可 UNAVAILABLE
    assert resolve_tts() is None

    monkeypatch.setenv("STEPWORK_TTS_VOICE", "voice-tone-X")
    p = resolve_tts()
    assert isinstance(p, StepFunTTSProvider)
    assert p.voice == "voice-tone-X"


def test_resolve_stepfun_reads_model_and_speed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEPWORK_TTS_PROVIDER", "stepfun")
    monkeypatch.setenv("STEPWORK_TTS_API_KEY", "sk-test")
    monkeypatch.setenv("STEPWORK_TTS_VOICE", "voice-tone-X")
    monkeypatch.setenv("STEPWORK_TTS_MODEL", "stepaudio-2-tts")
    monkeypatch.setenv("STEPWORK_TTS_SPEED", "1.5")
    p = resolve_tts()
    assert isinstance(p, StepFunTTSProvider)
    assert p.model == "stepaudio-2-tts"
    assert p.speed == 1.5


def test_resolve_stepfun_uses_workspace_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """设置页存的密钥走覆盖层，不落 SQLite。"""
    from worker.runtime.providers.resolve import CONFIG_OVERRIDES

    monkeypatch.setenv("STEPWORK_TTS_PROVIDER", "stepfun")
    monkeypatch.delenv("STEPWORK_TTS_API_KEY", raising=False)
    monkeypatch.delenv("STEPWORK_TTS_VOICE", raising=False)
    monkeypatch.setitem(
        CONFIG_OVERRIDES, "ws-tts", {"tts": {"apiKey": "sk-ws", "voice": "voice-tone-WS"}}
    )
    p = resolve_tts("ws-tts")
    assert isinstance(p, StepFunTTSProvider)
    assert p.api_key == "sk-ws"
    assert p.voice == "voice-tone-WS"
