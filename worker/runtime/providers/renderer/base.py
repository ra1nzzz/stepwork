"""Renderer Provider 协议（W6，ADR-009 插件对齐）。

``RendererProvider`` 为结构化协议（PEP 544，``runtime_checkable``）。
W6 交付"接口雏形"——一个内置 ``FFmpegRenderer``（capability
``render:vertical-caption-v1``）；完整独立进程插件运行时属 V0.2。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from worker.runtime.models import RenderResult, RenderSpec


@runtime_checkable
class RendererProvider(Protocol):
    """视频草稿渲染 Provider 协议。"""

    name: str
    capability: str

    def render(
        self,
        spec: RenderSpec,
        audio_uri: str,
        progress_cb: Callable[[float], None],
        cancel_event: Any,
    ) -> RenderResult:
        """渲染视频草稿。

        Args:
            spec: 渲染规格（含源版本、模板、分辨率）。
            audio_uri: 旁白音频 uri（由 TTS 合成或用户录音）。
            progress_cb: 进度回调（0.0–1.0）。
            cancel_event: 取消事件（``threading.Event``）；置位即终止。

        Returns:
            :class:`RenderResult`。
        """
        ...
