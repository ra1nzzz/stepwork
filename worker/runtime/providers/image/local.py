"""本地确定性配图（S2 占位实现，零依赖、离线可跑）。

**这不是插画。**它是一张把「幕文本 + 风格」直接画上去的 SVG 占位图，
存在的唯一理由是让整条流水线在没有厂商密钥、没有网络时也能端到端跑通，
并让每一幕的配图位置**肉眼可见**（渲出来就知道图挂在哪一幕）。

与 :class:`LocalTTSProvider` 同一套取舍（静音但时长真实 / 占位但位置真实），
但有一处**刻意不同**：:func:`resolve_image` 里 ``local`` **不是默认值** ——
必须显式 ``STEPWORK_IMAGE_PROVIDER=local`` 才启用。

原因是两者出错的代价不对称：TTS 静音顶多让你听不见，占位图一旦被当成
正式美术静默渲进成片，问题要到发布后才发现。宁可默认 ``UNAVAILABLE``
把人挡在门口。

产物是 SVG（纯文本，无需 Pillow / cairosvg），Chromium 可直接 ``<img>``
渲染，因此 Playwright 渲染器能吃它。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from typing import Any
from xml.sax.saxutils import escape

_DEFAULT_WIDTH = 1080
_DEFAULT_HEIGHT = 1920
#: 每行字数（中文竖排横写都取一个保守值，避免溢出画布）
_CHARS_PER_LINE = 14

_PALETTE: tuple[str, ...] = (
    "#f4f1ea",  # 纸白
    "#e8eef5",  # 冷灰蓝
    "#f6e7e3",  # 藕粉
    "#e9f0e6",  # 豆绿
    "#f3ead6",  # 米黄
    "#e6e6f0",  # 淡紫
)


def _pick_bg(seed: str) -> str:
    """按提示词哈希取底色：同输入 → 同配色（确定性）。"""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return _PALETTE[digest[0] % len(_PALETTE)]


def _wrap(text: str, per_line: int = _CHARS_PER_LINE) -> list[str]:
    """按字数硬折行（中文没有空格，按字切最稳）。"""
    compact = " ".join((text or "").split())
    if not compact:
        return []
    return [compact[i : i + per_line] for i in range(0, len(compact), per_line)]


def _build_svg(prompt: str, style: str, width: int, height: int) -> str:
    bg = _pick_bg(f"{prompt}\x00{style}")
    lines = _wrap(prompt)[:8]  # 最多 8 行，防止长文本溢出画布
    tspans = "\n".join(
        f'<tspan x="{width // 2}" dy="{0 if i == 0 else 72}">{escape(line)}</tspan>'
        for i, line in enumerate(lines)
    )
    body = (
        f'<text x="{width // 2}" y="{height // 2 - 40}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="56" fill="#1a1a1a">{tspans}</text>'
        if tspans
        else ""
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="{bg}"/>'
        f'{body}'
        f'<text x="{width // 2}" y="{height - 90}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="34" fill="#8a8a8a">'
        f'{escape(f"placeholder · {style}")}</text>'
        f"</svg>"
    )


class LocalImageProvider:
    """确定性本地占位配图（显式启用才生效，见模块 docstring）。"""

    name = "local-image"
    #: 本地生成不产生费用
    estimated_cost_per_1k = 0.0

    def __init__(
        self,
        out_dir: str | None = None,
        width: int = _DEFAULT_WIDTH,
        height: int = _DEFAULT_HEIGHT,
    ) -> None:
        self.out_dir = out_dir or os.path.join(
            tempfile.gettempdir(), "stepwork_images"
        )
        self.width = width
        self.height = height

    async def generate(self, prompt: str, opts: dict[str, Any] | None = None) -> str:
        opts = opts or {}
        out_dir = str(opts.get("out_dir") or self.out_dir)
        style = str(opts.get("style") or "illustration")
        width = int(opts.get("width") or self.width)
        height = int(opts.get("height") or self.height)
        os.makedirs(out_dir, exist_ok=True)

        # 文件名对（提示词, 风格, 尺寸）确定：同输入 → 同文件，可复用缓存
        key = f"{prompt}\x00{style}\x00{width}x{height}".encode()
        digest = hashlib.sha256(key).hexdigest()[:16]
        path = os.path.join(out_dir, f"img_local_{digest}.svg")
        await asyncio.to_thread(
            self._write_svg, path, prompt, style, width, height
        )
        return "file://" + path

    def _write_svg(
        self, path: str, prompt: str, style: str, width: int, height: int
    ) -> None:
        # 先写临时文件再原子改名：并发/中断不会留下半截 SVG 被误当有效缓存
        svg = _build_svg(prompt, style, width, height)
        tmp = f"{path}.part"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(svg)
        os.replace(tmp, path)
