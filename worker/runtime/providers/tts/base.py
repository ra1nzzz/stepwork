"""TTS Provider 协议（W6）。

``TTSProvider`` 为结构化协议（PEP 544，``runtime_checkable``）。
所有实现（local / cloud）只需满足 ``name`` + ``synthesize`` 签名即可。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class TTSError(RuntimeError):
    """TTS 调用失败。

    为什么继承 ``RuntimeError`` 而不是自定义 ``Exception``：handler 转译时
    用的是 ``f"{type(e).__name__}: {e}"``，类名会**直接进用户可见的错误
    串**——所以类名必须是人能读懂的词，不能是 ``TtsUpstreamFailure`` 这种
    只有开发者看得懂的代号。详见 :class:`ImageProviderError`（同因）。

    消息必须带**厂商响应体片段**：模型不存在 / 余额不足 / 音色 id 失效，
    原因全在 body 里，只报 ``HTTP 400`` 等于没报。
    """


@runtime_checkable
class TTSProvider(Protocol):
    """旁白（Text-to-Speech）Provider 协议。"""

    name: str

    async def synthesize(
        self, text: str, opts: dict[str, Any] | None = None
    ) -> str:
        """合成 ``text`` 为旁白音频，返回音频文件 uri。

        Args:
            text: 待合成文本（通常来自源 ``ContentVersion`` 的转写/脚本）。
            opts: 可选参数（如 ``out_dir``）。

        Returns:
            音频文件 uri（``file://...``）。
        """
        ...
