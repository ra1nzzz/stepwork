"""StepFun 复刻音色 TTS（``stepaudio-2.5-tts``）。

为什么单独一个文件（而不是塞进 :mod:`cloud`）：复刻音色走的是**非标准
端点** ``POST {base}/step_plan/v1/audio/speech``，请求体是
``{model, input, voice, response_format, sample_rate, speed, instruction}``，
响应是**音频裸字节**（不是 JSON 里塞 url）。这与 OpenAI 兼容的
``/audio/speech`` 不是同一份契约，所以按契约切文件 —— 与
:mod:`worker.runtime.providers.image.openai_compatible` 同样的切分理由。

三个必须处理的真实现象（都踩过，不是防御性编程）：

1. **命中错误缓存**：某些措辞（实测「群聊」、「浓缩成几分钟读完的要点」）
   会让接口对**不同 input** 返回**完全相同的固定音频**（内容是另一句话）。
   判据必须是 **MD5**：只比字节数会撞车（CBR 编码下实测误报过），白跑一轮
   重生成。命中即报错 —— 静默把错句渲进成片，代价远大于一次失败。
2. **语速被 ``instruction`` 带跑**：``instruction`` 里写「缓慢 / 舒缓」会盖过
   ``speed`` 参数（实测同 ``speed=1.25`` 下能差 1.4 倍），整片节奏忽快忽慢。
   故生成后按 **字/秒** 用 ``atempo`` 归一化（不变调）。
3. **网络不稳**：``api.stepfun.com`` 偶发 SSL / 超时，请求必须带重试。
4. **模型有权限表**：PLAN 端点只认 ``stepaudio-2.5-tts``。配成别的 TTS 模型名
   （``step-tts-2`` / ``step-tts-mini``）拿到的是 **404 + ``model_invalid``**
   —— 不是 403。只按状态码翻成「端点不存在」会把人引去查路径，而端点是对的
   （2026-09-13 真机实测），故错误映射要先认 body 里的 ``model_invalid``。

输出是 **mp3**（不是 WAV），时长探测因此依赖 ffmpeg —— 调用方
（``SynthesizeScenes``）已有 ffprobe 兜底，见其 ``_measure_duration``。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from worker.runtime.net import make_async_client
from worker.runtime.providers.tts.base import TTSError

logger = logging.getLogger("worker.runtime.providers.tts.stepfun")

_DEFAULT_BASE_URL = "https://api.stepfun.com"
_DEFAULT_MODEL = "stepaudio-2.5-tts"
#: 复刻音色专用路径（少了 ``step_plan`` 会直接 404）
_AUDIO_PATH = "/step_plan/v1/audio/speech"
_DEFAULT_SAMPLE_RATE = 24000
_DEFAULT_SPEED = 1.25

#: 语速归一化目标（字/秒）。取自实测成片节奏：慢于此显得拖，快于此听不清。
_TARGET_RATE = 6.0
#: 低于目标这个比例才提速（避免把正常波动也动一遍）
_SLOW_FACTOR = 0.87
#: 高于目标这个比例才降速
_FAST_FACTOR = 1.25
#: 降速时把速率压到目标的这个比例（留一点余量，不要一步压到目标）
_FAST_SETTLE = 1.08
#: atempo 单次可调范围；超出必须串联（ffmpeg 硬限制）
_ATEMPO_MIN = 0.5
_ATEMPO_MAX = 2.0
#: 变化小于此值不动（听不出来，却要多跑一次 ffmpeg + 多一次重编码）
_TEMPO_EPS = 0.03


def count_voiced_chars(text: str) -> int:
    """统计「计入语速」的字符数：中日韩统一表意文字 + 字母数字。

    只数汉字和字母数字，是因为标点、空格、引号都不占发音时长 —— 按总字符
    数算语速会把「标点多的幕」误判成超速。
    """
    return sum(1 for c in text if "\u4e00" <= c <= "\u9fff" or c.isalnum())


def atempo_filter(tempo: float) -> str:
    """把倍速拆成 ffmpeg ``atempo`` 过滤器链。

    ``atempo`` 单次只接受 0.5–2.0；超出范围必须**串联**而不是放大单次参数
    （单次传 2.5 会被静默截断到 2.0，节奏对不上却毫无报错）。
    """
    factors: list[float] = []
    rest = tempo
    while rest > _ATEMPO_MAX:
        factors.append(_ATEMPO_MAX)
        rest /= _ATEMPO_MAX
    while rest < _ATEMPO_MIN:
        factors.append(_ATEMPO_MIN)
        rest /= _ATEMPO_MIN
    factors.append(rest)
    return ",".join(f"atempo={f:.4f}" for f in factors)


def _write_atomic(path: Path, data: bytes) -> None:
    """先写 ``.part`` 再原子替换：中途失败不会留下半截文件被当缓存命中。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


class StepFunTTSProvider:
    """StepFun 复刻音色 TTS。

    Args:
        api_key: StepFun API Key（只来自 env / 设置页覆盖层，不落库）。
        voice: 复刻音色 id（形如 ``voice-tone-xxxx``），由
            ``STEPWORK_TTS_VOICE`` 或覆盖层提供。
        model: 语音模型，默认 ``stepaudio-2.5-tts``。
        base_url: 网关地址，默认 ``https://api.stepfun.com``。
        sample_rate / speed / response_format: 合成参数。
        client: 注入的 httpx 客户端（测试用）。
        runner: 注入的 :class:`FFmpegRunner`（测试用）；不注入时按
            ``STEPWORK_FFMPEG_BIN`` 解析。
        normalize_speed: 是否按 字/秒 做 ``atempo`` 归一化。关掉后
            ``instruction`` 带来的语速漂移会直接留在成片里。
    """

    name = "stepfun-tts"
    #: 单价未知（按 PLAN 计费，随音色/模型变动），明确为 None 而非编一个数
    estimated_cost_per_1k: float | None = None

    def __init__(
        self,
        api_key: str,
        voice: str,
        *,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        speed: float = _DEFAULT_SPEED,
        response_format: str = "mp3",
        client: Any = None,
        runner: Any = None,
        normalize_speed: bool = True,
        target_rate: float = _TARGET_RATE,
        max_attempts: int = 4,
        timeout: float = 180.0,
    ) -> None:
        self.api_key = api_key
        self.voice = voice
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.sample_rate = sample_rate
        self.speed = speed
        self.response_format = response_format
        self._client = client
        self._runner = runner
        self.normalize_speed = normalize_speed
        self.target_rate = target_rate
        self.max_attempts = max(1, max_attempts)
        self.timeout = timeout
        #: MD5 → 首次产出它的文本，用于「命中错误缓存」检测（见模块 docstring）
        self._seen: dict[str, str] = {}

    # -- 组装 ---------------------------------------------------------------

    def _url(self) -> str:
        return f"{self.base_url}{_AUDIO_PATH}"

    def _build_body(
        self, text: str, voice: str, instruction: str, speed: float
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "input": text,
            "voice": voice,
            "response_format": self.response_format,
            "sample_rate": self.sample_rate,
            "speed": speed,
        }
        # instruction 是「表演方向」（如「沉稳地讲述」），不是语速指令：
        # 写「缓慢 / 舒缓」会盖过 speed（实测差 1.4 倍），故交给归一化兜底
        if instruction:
            body["instruction"] = instruction
        return body

    def _target_path(
        self,
        voice: str,
        instruction: str,
        speed: float,
        text: str,
        out_dir: Path,
    ) -> Path:
        """同（音色, 指令, 语速, 文本）→ 同文件，可安全复用缓存。"""
        digest = hashlib.sha256(
            f"{voice}\x00{instruction}\x00{speed}\x00{text}".encode()
        ).hexdigest()[:16]
        return out_dir / f"tts_stepfun_{digest}.{self.response_format}"

    # -- 请求 ---------------------------------------------------------------

    async def _post(self, body: dict[str, Any]) -> bytes:
        """带重试地取回音频裸字节；失败抛 :class:`TTSError`（带 body 片段）。"""
        client = self._client or make_async_client()
        status = 0
        snippet = ""
        try:
            for attempt in range(1, self.max_attempts + 1):
                try:
                    resp = await client.post(
                        self._url(),
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=body,
                        timeout=self.timeout,
                    )
                except Exception as e:  # noqa: BLE001 - 网络抖动要重试
                    if attempt == self.max_attempts:
                        raise TTSError(
                            f"stepfun TTS 请求失败（{attempt}/{self.max_attempts}）："
                            f"{type(e).__name__}: {e}"
                        ) from None
                    await asyncio.sleep(2 * attempt)
                    continue
                status = resp.status_code
                if status < 500 or attempt == self.max_attempts:
                    break
                snippet = _body_snippet(resp)
                logger.warning(
                    "stepfun TTS HTTP %s，重试 %s/%s：%s",
                    status,
                    attempt,
                    self.max_attempts,
                    snippet,
                )
                await asyncio.sleep(2 * attempt)
        finally:
            if self._client is None:
                await client.aclose()

        if status >= 400:
            raise TTSError(_error_message(status, _body_snippet(resp), self.model))
        data = resp.content
        if not data:
            raise TTSError(f"stepfun TTS 返回空音频（HTTP {status}）")
        return data

    def _guard_cached_audio(self, data: bytes, text: str) -> None:
        """不同文本得到同一段音频 = 命中错误缓存（判据用 MD5，不用字节数）。"""
        digest = hashlib.md5(data).hexdigest()  # noqa: S324 - 只做去重判同
        seen = self._seen.get(digest)
        if seen is not None and seen != text:
            raise TTSError(
                "stepfun TTS 命中错误缓存：不同文本返回了完全相同的音频"
                f"（MD5 {digest[:12]}）—— 换措辞后重试；实测「群聊」、"
                "「浓缩成几分钟读完的要点」会触发"
            )
        self._seen[digest] = text

    # -- 语速归一化 ---------------------------------------------------------

    def _runner_or_resolve(self) -> Any:
        if self._runner is not None:
            return self._runner
        # 延迟导入：providers.resolve 会导入本模块，模块级导入即成环
        from worker.runtime.providers.resolve import ffmpeg_runner

        return ffmpeg_runner()

    def _probe_duration(self, path: Path, runner: Any) -> float:
        try:
            return float(runner.probe(str(path)))
        except Exception:  # noqa: BLE001 - 探不到就不管，宁可不归一也不失败
            return 0.0

    def _tempo_for(self, rate: float) -> float:
        """按实测语速算 atempo 倍率；1.0 表示不用动。"""
        if rate < self.target_rate * _SLOW_FACTOR:
            return min(1.4, self.target_rate / rate)
        if rate > self.target_rate * _FAST_FACTOR:
            return max(0.85, (self.target_rate * _FAST_SETTLE) / rate)
        return 1.0

    def _apply_tempo(self, path: Path, tempo: float, runner: Any) -> None:
        # 临时文件必须保留扩展名：ffmpeg 按扩展名猜输出封装，
        # 丢掉 .mp3 会直接报「Unable to find a suitable output format」
        tmp = str(path) + ".norm" + path.suffix
        runner.run(
            [
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-af",
                atempo_filter(tempo),
                "-b:a",
                "128k",
                tmp,
            ],
            lambda _p: None,
            threading.Event(),
            timeout_sec=180,
        )
        os.replace(tmp, path)

    async def _normalize(self, path: Path, text: str) -> None:
        runner = self._runner_or_resolve()
        if not getattr(runner, "available", False):
            logger.warning(
                "stepfun TTS：ffmpeg 不可用，跳过语速归一化（instruction 可能让"
                "整片语速忽快忽慢）"
            )
            return
        duration = await asyncio.to_thread(self._probe_duration, path, runner)
        chars = count_voiced_chars(text)
        if duration <= 0 or chars == 0:
            return
        tempo = self._tempo_for(chars / duration)
        if abs(tempo - 1.0) < _TEMPO_EPS:
            return
        try:
            await asyncio.to_thread(self._apply_tempo, path, tempo, runner)
        except Exception as e:  # noqa: BLE001 - 归一化是增强，失败不该毁掉配音
            logger.warning("stepfun TTS：语速归一化失败，保留原音频（%s: %s）", type(e).__name__, e)

    # -- 协议入口 -----------------------------------------------------------

    async def synthesize(self, text: str, opts: dict[str, Any] | None = None) -> str:
        opts = opts or {}
        out_dir = Path(opts.get("out_dir") or (Path(tempfile.gettempdir()) / "stepwork_tts"))
        voice = str(opts.get("voice") or self.voice)
        # handler 传的是 emotion（分幕字段），StepFun 侧字段名是 instruction
        instruction = str(opts.get("emotion") or opts.get("instruction") or "")
        try:
            speed = float(opts.get("speed") or self.speed)
        except (TypeError, ValueError):
            speed = self.speed

        path = self._target_path(voice, instruction, speed, text, out_dir)
        if not await asyncio.to_thread(path.is_file):
            data = await self._post(self._build_body(text, voice, instruction, speed))
            self._guard_cached_audio(data, text)
            await asyncio.to_thread(_write_atomic, path, data)
            if self.normalize_speed:
                await self._normalize(path, text)
        return "file://" + str(path)


def _body_snippet(resp: Any, limit: int = 300) -> str:
    try:
        raw = resp.text
    except Exception:  # noqa: BLE001 - 有些响应体读不出来（已消费的流）
        return ""
    return (raw or "").strip()[:limit]


def _error_message(status: int, snippet: str, model: str = "") -> str:
    """把状态码翻成人能行动的提示；body 片段永远带上。

    优先认 ``model_invalid``：模型不被 PLAN 端点接受时，StepFun 返回的是
    **404 + ``model_invalid``**（不是 403）。只按状态码翻会把排查方向引到
    端点路径上，而端点是对的、换个模型就好。
    """
    if "model_invalid" in snippet:
        got = f"，当前配的是 {model}" if model else ""
        return (
            f"stepfun TTS HTTP {status}（模型不可用{got}：该 key 在 PLAN 端点"
            f"没有这个模型 —— 复刻音色只能用 {_DEFAULT_MODEL}，"
            f"`step-tts-2` / `step-tts-mini` 等模型名在此端点无权）：{snippet}"
        )
    hint = {
        401: "密钥无效",
        403: "密钥无权访问该端点（复刻音色需 /step_plan/ 路径）",
        402: "余额不足 / 配额用尽",
        404: "端点或模型不存在（复刻音色走 /step_plan/v1/audio/speech）",
        429: "触发限流",
    }.get(status, "")
    tail = f"：{snippet}" if snippet else ""
    return f"stepfun TTS HTTP {status}{('（' + hint + '）') if hint else ''}{tail}"
