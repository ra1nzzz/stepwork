"""Image Provider 协议（S2 配图阶段）。

``ImageProvider`` 为结构化协议（PEP 544，``runtime_checkable``），照
:mod:`worker.runtime.providers.ai.base` 的范式：新增厂商只需满足
``name`` + ``generate`` 签名，不必改动 dispatch。

**为什么这一层必须「接口先于厂商」**（而不是先接一家再抽象）：

- StepFun 生图 **2026-10-10 下线**且官方无替代模型，选型未定
  （通义万相 / CogView-4 / 硅基流动 / 本地 SDXL 待实测）。
- 先写死某家，接口就会被那家的参数形状带偏（尺寸枚举、风格参数、
  异步任务轮询），换厂商时等于重写。
- 因此本模块**只定义契约**；厂商适配器等选型定了再各加一个文件。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ImageProvider(Protocol):
    """配图（文生图）Provider 协议。"""

    name: str

    async def generate(self, prompt: str, opts: dict[str, Any] | None = None) -> str:
        """按提示词生成一张配图，返回图片文件 uri。

        Args:
            prompt: 画面描述（调用方负责把「幕文本 + 美术风格」拼成提示词）。
            opts: 可选参数（``out_dir`` / ``size`` / ``style`` / ``negative``
                等）。实现必须**容忍未知键**，未识别的一律忽略——否则新增
                一个厂商就得改所有调用方。

        Returns:
            图片文件 uri（``file://...``）。

        Raises:
            实现相关异常：由调用方（handler）统一转译为领域错误；本项目
            要求生图失败**可见**（任务进 FAILED），绝不静默出空片。
        """
        ...
