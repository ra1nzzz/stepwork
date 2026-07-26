"""渲染模板与画幅注册表（PRD-REN-005）。

此前 :class:`~worker.runtime.providers.renderer.ffmpeg.FFmpegRenderer` 完全
忽略 ``spec.template``——只有一条硬编码 argv，UI 下拉里切换模板渲出的画面
完全相同，属「看起来生效、实际无效」的误导。本模块把模板变成**真实数据**：
每个模板给出自己的背景、字号、字幕位置与默认画幅，渲染器据此生成滤镜。

同时提供画幅预设（PRD-REN-005：9:16 优先，另支持 16:9、1:1）。

新增模板只需在 :data:`TEMPLATES` 里加一项；未注册的 template 由
``resolve_template`` 抛 ``KeyError``，handler 转 ``INVALID_ARGUMENT``——
绝不静默回退成另一个模板（那正是旧行为的问题）。
"""

from __future__ import annotations

from typing import NamedTuple

# 画幅预设：aspect -> (宽, 高)。9:16 为 MVP 主路径。
ASPECT_PRESETS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}

DEFAULT_ASPECT = "9:16"


class Template(NamedTuple):
    """一个渲染模板的可视参数。"""

    id: str
    label: str
    #: 背景色（ffmpeg color 滤镜取值）
    background: str
    #: 字幕字号
    font_size: int
    #: 字幕 y 坐标表达式（ffmpeg drawtext 语法）
    caption_y: str
    #: 字幕颜色
    font_color: str
    #: 该模板的默认画幅
    default_aspect: str


TEMPLATES: dict[str, Template] = {
    "vertical-caption-v1": Template(
        id="vertical-caption-v1",
        label="竖屏字幕",
        background="navy",
        font_size=48,
        caption_y="h-120",  # 底部字幕条
        font_color="white",
        default_aspect="9:16",
    ),
    "vertical-story-v1": Template(
        id="vertical-story-v1",
        label="竖屏故事",
        background="black",
        font_size=64,  # 更大字号
        caption_y="(h-text_h)/2",  # 居中大字
        font_color="#FFD966",
        default_aspect="9:16",
    ),
    "landscape-caption-v1": Template(
        id="landscape-caption-v1",
        label="横屏字幕",
        background="#101820",
        font_size=42,
        caption_y="h-96",
        font_color="white",
        default_aspect="16:9",
    ),
    "square-caption-v1": Template(
        id="square-caption-v1",
        label="方图字幕",
        background="#1B2430",
        font_size=44,
        caption_y="h-100",
        font_color="white",
        default_aspect="1:1",
    ),
}


def resolve_template(template_id: str) -> Template:
    """按 id 取模板；未注册即 ``KeyError``（handler 转 INVALID_ARGUMENT）。"""
    try:
        return TEMPLATES[template_id]
    except KeyError as exc:
        known = ", ".join(sorted(TEMPLATES))
        raise KeyError(
            f"unknown template {template_id!r}; known templates: {known}"
        ) from exc


def resolve_resolution(aspect: str) -> tuple[int, int]:
    """按画幅名取分辨率；未知画幅即 ``KeyError``。"""
    try:
        return ASPECT_PRESETS[aspect]
    except KeyError as exc:
        known = ", ".join(ASPECT_PRESETS)
        raise KeyError(
            f"unknown aspect {aspect!r}; known aspects: {known}"
        ) from exc


def list_templates() -> list[dict[str, str]]:
    """供前端/CLI 展示的模板清单（id / 标签 / 默认画幅）。"""
    return [
        {"id": t.id, "label": t.label, "default_aspect": t.default_aspect}
        for t in TEMPLATES.values()
    ]
