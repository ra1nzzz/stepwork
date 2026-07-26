"""分析 Prompt 构造（W4，Batch 2）。

把"源素材 + 品牌画像"组装成给 AI 的提示词，并要求其输出
符合 :data:`ANALYSIS_SCHEMA` 的结构。
"""

from __future__ import annotations

from typing import Any

from worker.runtime.analysis.schema import ANALYSIS_SCHEMA

_MAX_TEXT_CHARS = 4000


def build_analysis_prompt(source_meta: dict[str, Any], brand: dict[str, Any] | None = None) -> str:
    """构造内容分析提示词。

    Args:
        source_meta: 至少含 ``text``（待分析文本，如转写稿）。
        brand: 可选品牌画像（``name`` / ``pillars`` 等）。

    Returns:
        完整提示词字符串。
    """
    text = str(source_meta.get("text", ""))[:_MAX_TEXT_CHARS]
    brand = brand or {}
    brand_name = brand.get("name", "未知品牌")
    pillars = brand.get("pillars", [])

    # 精确分析（PRD-ANA-003）：附场景时间线，使 structure / key_points
    # 能引用镜头切点与关键帧时间戳（PRD-ANA-005 来源位置）。
    scenes = source_meta.get("scenes") or []
    scene_block = ""
    if scenes:
        lines = "\n".join(
            f"- 场景{s['index'] + 1}: {s['start']:.1f}s–{s['end']:.1f}s"
            f"（关键帧 {s['keyframe_sec']:.1f}s）"
            for s in scenes
        )
        scene_block = (
            f"\n以下是该素材的场景切分时间线（共 {len(scenes)} 个镜头）：\n"
            f"----\n{lines}\n----\n"
            f"请在 structure 中按场景顺序描述，并尽量在 key_points 里带上"
            f"对应时间戳（如「12.3s」），便于跳转来源位置。\n"
        )

    schema_hint = ", ".join(ANALYSIS_SCHEMA["required"])
    return (
        f"你是一名为「{brand_name}」做内容分析的助手。\n"
        f"品牌内容支柱：{pillars}\n\n"
        f"以下是素材转写稿：\n----\n{text}\n----\n"
        f"{scene_block}\n"
        f"请基于以上输出 JSON，字段必须包含：{schema_hint}。\n"
        f"sentiment 取值为 positive / neutral / negative；"
        f"confidence 为 0~1 的置信度；"
        f"topics / suggested_tags / key_points 为字符串数组；"
        f"hook 为素材开头钩子（无明显钩子时为 null）；"
        f"structure 为内容结构骨架（字符串数组，按叙事顺序）；"
        f"risks 为风险点（字符串数组，如事实存疑 / 合规 / 版权风险）。"
    )
