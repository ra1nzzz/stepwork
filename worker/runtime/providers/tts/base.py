"""TTS Provider 协议（W6）。

``TTSProvider`` 为结构化协议（PEP 544，``runtime_checkable``）。
所有实现（local / cloud）只需满足 ``name`` + ``synthesize`` 签名即可。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


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
