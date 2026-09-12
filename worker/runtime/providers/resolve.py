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

import importlib.util
import os
import threading
from typing import Any, NamedTuple
from urllib.parse import urlparse

from worker.runtime.analysis.scene import FFmpegSceneDetector, SceneDetector
from worker.runtime.providers.ai.base import AIProvider
from worker.runtime.providers.ai.cloud import CloudAIProvider
from worker.runtime.providers.ai.openai_compatible import (
    OpenAICompatibleProvider,
)
from worker.runtime.providers.asr.base import ASRProvider
from worker.runtime.providers.asr.cloud import CloudASRProvider
from worker.runtime.providers.asr.local import LocalASRProvider
from worker.runtime.providers.image.base import ImageProvider
from worker.runtime.providers.image.local import LocalImageProvider
from worker.runtime.providers.image.openai_compatible import (
    OpenAICompatibleImageProvider,
)
from worker.runtime.providers.publish.base import PublishProvider
from worker.runtime.providers.publish.opencli import OpenCliPublishProvider
from worker.runtime.providers.renderer.base import RendererProvider
from worker.runtime.providers.renderer.ffmpeg import FFmpegRenderer
from worker.runtime.providers.tts.base import TTSProvider
from worker.runtime.providers.tts.cloud import CloudTTSProvider
from worker.runtime.providers.tts.local import LocalTTSProvider
from worker.runtime.providers.tts.stepfun import StepFunTTSProvider
from worker.runtime.render.ffmpeg_runner import FFmpegRunner


def _env(key: str) -> str | None:
    """读取环境变量，空串视为未设置。"""
    v = os.environ.get(key)
    return v if v else None


def _has_module(name: str) -> bool:
    """可选依赖探测：包已安装才为真（不触发导入副作用）。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


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


def _build_whisper(workspace_id: str | None) -> ASRProvider:
    """构造 faster-whisper Provider（模型 size 取 env / 覆盖层 / 默认 small）。

    调用前须确保 ``faster_whisper`` 已安装（``_has_module`` 守卫）。
    """
    ov = _override_for(workspace_id, "asr")
    model = _env("STEPWORK_ASR_MODEL") or str(ov.get("model") or "") or "small"
    from worker.runtime.providers.asr.whisper import FasterWhisperASRProvider

    return FasterWhisperASRProvider(model_size=model)


def resolve_asr(workspace_id: str | None = None) -> ASRProvider | None:
    """按 ``STEPWORK_ASR_PROVIDER`` 解析 ASR Provider。

    - ``auto``（默认）：装了 ``faster-whisper``（可选依赖 ``.[asr]``）就用
      本地真实识别，否则回退确定性草稿转写。安装即启用——装可选引擎
      本身就是用户的启用信号，无需再改配置。
    - ``local``：强制离线确定性草稿转写（canned demo）。测试/纯离线复现
      需确定结果时显式选它，绕开 auto 的真实引擎优先。
    - ``whisper``（``faster-whisper``）：显式真实识别；未安装则回退
      ``None`` → ``UNAVAILABLE``（区别于 auto 的静默回退 demo）。模型 size
      取 ``STEPWORK_ASR_MODEL`` / 覆盖层 / 默认 ``small``。
    - ``cloud``：需 ``STEPWORK_ASR_API_KEY`` + ``STEPWORK_ASR_BASE_URL``，
      否则回退 ``None``。

    若 env 缺失，则回退到 ``workspace_id`` 对应的密钥覆盖层
    （来自设置页保存的密钥，仅存内存）。
    """
    kind = (_env("STEPWORK_ASR_PROVIDER") or "auto").lower()
    if kind == "auto":
        # 默认：真实引擎优先，缺失静默回退确定性 demo（永不返回 None）
        if _has_module("faster_whisper"):
            return _build_whisper(workspace_id)
        return LocalASRProvider()
    if kind == "local":
        return LocalASRProvider()
    if kind in ("whisper", "faster-whisper", "faster_whisper"):
        # 显式选真实引擎：包缺失即回退 None（handler → UNAVAILABLE），
        # 绝不因缺依赖崩掉解析。
        if not _has_module("faster_whisper"):
            return None
        return _build_whisper(workspace_id)
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
    """按 ``STEPWORK_TTS_PROVIDER`` 解析 TTS Provider（W6 / Tranche 3）。

    - ``local``（默认）：离线确定性 WAV（静音但时长真实），满足渲染证伪。
    - ``edge``（``edge-tts``）：微软在线神经语音（可选依赖 ``.[tts]``；
      未安装则回退 ``None`` → ``UNAVAILABLE``）；声线取
      ``STEPWORK_TTS_VOICE`` / 覆盖层 / provider 默认。
    - ``stepfun``：复刻音色（``stepaudio-2.5-tts``），需
      ``STEPWORK_TTS_API_KEY`` + ``STEPWORK_TTS_VOICE``（音色 id），
      否则回退 ``None``。
    - ``cloud``：需 ``STEPWORK_TTS_API_KEY`` + ``STEPWORK_TTS_BASE_URL``，
      否则回退 ``None``。
    env 缺失时回退到 ``workspace_id`` 的密钥覆盖层。
    """
    kind = (_env("STEPWORK_TTS_PROVIDER") or "local").lower()
    if kind == "local":
        return LocalTTSProvider()
    if kind in ("edge", "edge-tts", "edge_tts"):
        # 可选真实语音：缺包即回退 None（handler → UNAVAILABLE）。
        # 声线取 env / 覆盖层 / provider 默认。
        if not _has_module("edge_tts"):
            return None
        ov = _override_for(workspace_id, "tts")
        voice = _env("STEPWORK_TTS_VOICE") or str(ov.get("voice") or "") or None
        from worker.runtime.providers.tts.edge import EdgeTTSProvider

        return EdgeTTSProvider(voice=voice)
    if kind in ("stepfun", "stepfun-tts", "stepfun_tts"):
        ov = _override_for(workspace_id, "tts")
        key = _env("STEPWORK_TTS_API_KEY") or str(ov.get("apiKey") or "")
        voice = _env("STEPWORK_TTS_VOICE") or str(ov.get("voice") or "")
        # 复刻音色缺音色 id 等于没法说话：宁可 UNAVAILABLE 也别拿默认音色出片
        if not key or not voice:
            return None
        kwargs: dict[str, Any] = {}
        tts_model = _env("STEPWORK_TTS_MODEL") or str(ov.get("model") or "")
        if tts_model:
            kwargs["model"] = tts_model
        base_url = _env("STEPWORK_TTS_BASE_URL") or str(ov.get("baseUrl") or "")
        if base_url:
            kwargs["base_url"] = base_url
        # 非法语速视为未配置，回落到 provider 默认值（1.25）
        raw_speed = _env("STEPWORK_TTS_SPEED") or ov.get("speed")
        if raw_speed not in (None, ""):
            try:
                kwargs["speed"] = float(raw_speed)
            except (TypeError, ValueError):
                pass
        return StepFunTTSProvider(api_key=key, voice=voice, **kwargs)
    if kind == "cloud":
        ov = _override_for(workspace_id, "tts")
        key = _env("STEPWORK_TTS_API_KEY") or str(ov.get("apiKey") or "")
        url = _env("STEPWORK_TTS_BASE_URL") or str(ov.get("baseUrl") or "")
        model = _env("STEPWORK_TTS_MODEL") or str(ov.get("model") or "") or None
        if not key or not _valid_base_url(url):
            return None
        # PRD-REN-002：每千字符单价来自 env / 设置页覆盖层；非法值视为未配置
        raw_cost = _env("STEPWORK_TTS_COST_PER_1K") or ov.get("costPer1k")
        cost: float | None = None
        if raw_cost not in (None, ""):
            try:
                cost = float(raw_cost)
            except (TypeError, ValueError):
                cost = None
        return CloudTTSProvider(
            api_key=key, base_url=url, model=model, cost_per_1k=cost
        )
    return None


def ffmpeg_runner() -> FFmpegRunner:
    """按 ``STEPWORK_FFMPEG_BIN`` / PATH 构造 :class:`FFmpegRunner`。

    统一入口，避免各处手写 ``FFmpegRunner()`` —— 那会退化成只查 PATH，
    而 WinGet 装的 ffmpeg 常常不在 PATH 里（本机即如此），于是「明明装了
    ffmpeg 却报 UNAVAILABLE」。
    """
    return FFmpegRunner(bin_path=_env("STEPWORK_FFMPEG_BIN"))


def _build_renderer(kind: str, runner: FFmpegRunner) -> RendererProvider | None:
    """按 kind 构造渲染器；未知 kind 返回 ``None``（交回默认 provider）。"""
    if kind in ("playwright", "pw"):
        if not _has_module("playwright"):
            return None
        from worker.runtime.providers.renderer.playwright import PlaywrightRenderer

        return PlaywrightRenderer(runner)
    if kind in ("ffmpeg", ""):
        return FFmpegRenderer(runner)
    return None


def resolve_renderer() -> RendererProvider | None:
    """按 ``STEPWORK_RENDER_PROVIDER`` 解析渲染器（默认仍是 ffmpeg）。

    - ``ffmpeg``（默认）：W6 内置 vertical-caption-v1 字幕渲染器。
    - ``playwright``（S1）：Playwright 逐帧渲染（HTML 视觉稿 → 管道直连
      ffmpeg）。``playwright`` 包缺失即回退 ``None`` → handler 转
      ``UNAVAILABLE``——**绝不静默回退成 ffmpeg 渲出另一条片子**。

    未知 kind 同样返回 ``None``（→ UNAVAILABLE）：拼错变量名不该静默渲出
    一条「看起来成功、其实用错渲染器」的片子。

    ffmpeg 可执行名可用 ``STEPWORK_FFMPEG_BIN`` 显式指定（WinGet 安装的
    ffmpeg 常不在 PATH 里）；未指定则走 ``shutil.which("ffmpeg")``。
    """
    runner = ffmpeg_runner()
    kind = (_env("STEPWORK_RENDER_PROVIDER") or "ffmpeg").lower()
    return _build_renderer(kind, runner)


def renderer_from_hint(
    hint: dict[str, Any] | str | None, runner: FFmpegRunner | None = None
) -> RendererProvider | None:
    """从 per-request 提示（``payload.renderer``）构建渲染器。

    S1 只支持 env 变量切换渲染器，意味着**同一个进程里无法为不同项目选不同
    渲染器**——GUI 上一个工作区想用插画版、另一个想用字幕版就做不到，P4
    （GUI 与 CLI 同为一等公民）会落空。本函数是补上这个缺口。

    ``hint`` 两种形态（与 :func:`ai_provider_from_hint` 同构）：

    - 字符串：``"playwright"`` / ``"ffmpeg"``
    - 字典：``{"kind": "playwright"}``

    缺失 / 空 / 未知 kind 均返回 ``None`` → 调用方回落到 ``deps.renderer``。
    注意：**不**接受 ``base_url`` / 密钥等字段——渲染是本机能力，不该被
    per-request 提示注入外部地址。

    Args:
        hint: 提示（字符串或 ``{"kind": ...}`` 字典）。
        runner: 复用调用方已有的 :class:`FFmpegRunner`（同一份 ffmpeg 路径与
            测试注入的 fake）。不传则按 ``STEPWORK_FFMPEG_BIN`` / PATH 新建。
    """
    if not hint:
        return None
    kind = hint if isinstance(hint, str) else str(hint.get("kind", "") or "")
    kind = kind.strip().lower()
    if not kind:
        return None
    return _build_renderer(
        kind, runner or ffmpeg_runner()
    )


def resolve_scene_detector() -> SceneDetector | None:
    """PRD-ANA-003 精确分析的场景切分器（ffmpeg 场景检测滤镜）。

    ffmpeg 缺失时 ``available=False``，handler 精确模式转 ``UNAVAILABLE``；
    始终返回实例（而非 None），由 handler 按 ``available`` 决定是否可用。
    切点灵敏度取 ``STEPWORK_SCENE_THRESHOLD``（默认 0.4）。
    """
    thr = _env("STEPWORK_SCENE_THRESHOLD")
    threshold = float(thr) if thr else 0.4
    return FFmpegSceneDetector(threshold=threshold)


class ImagePreset(NamedTuple):
    """厂商预置：只装**契约之外的差异**，其余走 OpenAI 通用契约。

    换厂商 = 改一个 env，而不是改代码 —— 选型未定期间这件事会反复发生。
    """

    base_url: str
    model: str
    size: str
    #: 尺寸字段名（OpenAI 标准是 ``size``，硅基流动文档写 ``image_size``）
    size_key: str = "size"


#: 2026-09-09 现状：StepFun 生图已无模型可用（``/v1/models`` 里只剩图生图
#: ``step-image-edit-2``），故不设 stepfun 预置。
IMAGE_PRESETS: dict[str, ImagePreset] = {
    # 智谱 CogView-4：同步返回 data[0].url；尺寸须 16 整除、≤2^21 px，
    # 1088x1920 是能取到的最接近 9:16 的合法值（1080 不能被 16 整除）。
    "cogview": ImagePreset(
        "https://open.bigmodel.cn/api/paas/v4", "cogview-4", "1088x1920"
    ),
    # 硅基流动：Kolors 是长期免费档；竖屏用 STEPWORK_IMAGE_SIZE=720x1440。
    "siliconflow": ImagePreset(
        "https://api.siliconflow.cn/v1",
        "Kwai-Kolors/Kolors",
        "1024x1024",
        size_key="image_size",
    ),
}

#: 没有预置、但走同一份 OpenAI 契约的网关（one-api / 自建转发 / OpenAI 本身）
_GENERIC_IMAGE_KINDS = ("openai", "openai-compatible", "openai_compatible")


def _build_image(kind: str | None, workspace_id: str | None) -> ImageProvider | None:
    """按厂商 kind 构建 OpenAI 兼容配图 Provider；配置不全一律 ``None``。

    env 缺失时回退到 ``workspace_id`` 的密钥覆盖层（设置页保存，仅内存）。
    """
    ov = _override_for(workspace_id, "image")
    key = _env("STEPWORK_IMAGE_API_KEY") or str(ov.get("apiKey") or "")
    if not key:
        return None

    preset = IMAGE_PRESETS.get(kind or "")
    if preset is None:
        # 无预置 → base_url 必须自己给，否则无从发请求
        url = _env("STEPWORK_IMAGE_BASE_URL") or str(ov.get("baseUrl") or "")
        model = _env("STEPWORK_IMAGE_MODEL") or ov.get("model")
        size = _env("STEPWORK_IMAGE_SIZE") or "1024x1024"
        size_key = _env("STEPWORK_IMAGE_SIZE_KEY") or "size"
    else:
        url = (
            _env("STEPWORK_IMAGE_BASE_URL")
            or str(ov.get("baseUrl") or "")
            or preset.base_url
        )
        model = _env("STEPWORK_IMAGE_MODEL") or ov.get("model") or preset.model
        size = _env("STEPWORK_IMAGE_SIZE") or preset.size
        size_key = _env("STEPWORK_IMAGE_SIZE_KEY") or preset.size_key

    if not _valid_base_url(url):
        return None
    return OpenAICompatibleImageProvider(
        api_key=key,
        base_url=url,
        model=model,
        size=size,
        size_key=size_key,
    )


def resolve_image(workspace_id: str | None = None) -> ImageProvider | None:
    """按 ``STEPWORK_IMAGE_PROVIDER`` 解析配图 Provider（S2）。

    - **未设置（默认）→ ``None``**：配图一律 ``UNAVAILABLE``。宁可挡住，
      也不让占位图被当成正式美术静默渲进成片。
    - ``local``：确定性 SVG **占位图**（显式启用才生效，用于端到端联调）。
      不是插画，只是让「图挂在哪一幕」肉眼可见。
    - ``cogview`` / ``siliconflow``：走 :class:`ImagePreset` 预置，只需
      ``STEPWORK_IMAGE_API_KEY``。
    - ``openai`` / ``openai-compatible``：任意 OpenAI 兼容网关，需额外给
      ``STEPWORK_IMAGE_BASE_URL``。

    任一厂商都可用 ``STEPWORK_IMAGE_MODEL`` / ``STEPWORK_IMAGE_SIZE`` /
    ``STEPWORK_IMAGE_SIZE_KEY`` 覆盖预置值；配置不全返回 ``None``
    （handler → ``UNAVAILABLE``），绝不把空密钥打到线上。

    **不覆盖**通义万相 / ``qwen-image``：官方明确不支持 OpenAI 兼容模式
    （DashScope 原生端点，尺寸 ``W*H`` 星号、响应结构也不同），真选它再
    单开适配器 —— 不为了「凑齐三家」写一份没验证过的实现。
    """
    kind = (_env("STEPWORK_IMAGE_PROVIDER") or "").strip().lower()
    if kind == "local":
        return LocalImageProvider()
    if kind in IMAGE_PRESETS or kind in _GENERIC_IMAGE_KINDS:
        return _build_image(kind, workspace_id)
    return None


def image_provider_from_hint(
    hint: dict[str, Any] | str | None,
    workspace_id: str | None = None,
) -> ImageProvider | None:
    """从 per-request 提示（``payload.imageProvider``）构建配图 Provider。

    与 :func:`renderer_from_hint` 同构：env 只能全局切换，而同一进程里不同
    项目可能要用不同厂商/风格。缺失 / 空 / 未知 → ``None``（调用方回落到
    ``deps.image``）。

    厂商密钥**只**从 env / 覆盖层取，不接受 hint 内联 —— 密钥不该跟着
    每条命令的 payload 走（会进命令日志与审计表）。
    """
    if not hint:
        return None
    kind = hint if isinstance(hint, str) else str(hint.get("kind", "") or "")
    kind = kind.strip().lower()
    if not kind:
        return None
    if kind == "local":
        return LocalImageProvider()
    if kind in IMAGE_PRESETS or kind in _GENERIC_IMAGE_KINDS:
        return _build_image(kind, workspace_id)
    return None


def resolve_publish_provider() -> PublishProvider | None:
    """按 ``STEPWORK_PUBLISH_PROVIDER`` 解析发布 Provider（S7）。

    - **未设置（默认）→ ``None``**：发布能力显式关闭。与
      :func:`resolve_image` 同一条规矩 —— 宁可挡住，也不给一个「看起来能用」
      的出口。发布比配图更该如此：假装的填充会让人以为内容已经排上了。
    - ``opencli``：经 PATH 调 OpenCLI（ADR-012 的底座候选），只走 fill/draft
      路径（ADR-008）。

    **可用性不在这里判断** —— 解析期只能看「装没装」，看不到「daemon 起没起、
    登录没有」，而那两种状态才是用户真正会撞上的。所以这里只负责**构建**，
    三态判别交给 :meth:`PublishProvider.probe` 在运行期回答。

    未知取值返回 ``None``（→ ``UNAVAILABLE``），不猜、不回落 —— 拼错的渠道名
    若能「凑合跑」，错误就会一直留在配置里。用 ``STEPWORK_OPENCLI_BIN`` 可指定
    可执行文件名或路径（默认 ``opencli``）。
    """
    kind = (_env("STEPWORK_PUBLISH_PROVIDER") or "").strip().lower()
    if kind == "opencli":
        return OpenCliPublishProvider(binary=_env("STEPWORK_OPENCLI_BIN") or "opencli")
    return None
