"""TopicProposal prompt + schema（W5）。

复用 W4 ``AIProvider.complete(prompt, schema)`` 范式；schema 约束结构化输出。
"""
from __future__ import annotations

from typing import Any

TOPIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "angles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "rationale": {"type": "string"},
                    "hook": {"type": "string"},
                    # PRD-SCR-001：每个角度须含受众、观点、差异与风险
                    "audience": {"type": ["string", "null"]},
                    "stance": {"type": ["string", "null"]},
                    "risks": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "id", "title", "rationale", "hook",
                    "audience", "stance", "risks",
                ],
            },
        }
    },
    "required": ["angles"],
}


def build_topic_prompt(
    source_text: str, count: int, brand_block: str | None = None
) -> str:
    """构造差异化角度生成提示（取素材前 2000 字）。

    Args:
        source_text: 源素材文本。
        count: 期望角度数。
        brand_block: 可选品牌画像注入块（Tranche 2，PRD-BRD-002；
            由 ``handlers.brand.format_brand_prompt_block`` 生成）。
    """
    excerpt = source_text[:2000]
    brand = f"{brand_block}\n\n" if brand_block else ""
    return (
        f"{brand}"
        f"基于以下素材，提出 {count} 个差异化的短视频选题角度。\n"
        "每个角度必须包含以下字段（PRD-SCR-001：受众、观点、差异、风险）：\n"
        "- title：清晰标题\n"
        "- rationale：与其它角度的差异化依据\n"
        "- hook：能抓住注意力的开头钩子\n"
        "- audience：这个角度面向的具体受众\n"
        "- stance：核心观点/立场（一句话表明主张）\n"
        "- risks：该角度的风险点（字符串数组，如事实存疑 / 合规 / 版权；"
        "无明显风险时给空数组）\n\n"
        f"素材：\n{excerpt}\n\n以 JSON 返回，结构见 schema。"
    )
