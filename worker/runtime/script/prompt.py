"""Script prompt + schema（W5）。

复用 W4 ``AIProvider.complete(prompt, schema)`` 范式；脚本正文为编辑器原生
（TipTap/ProseMirror JSON 或纯文本），落 ``content_versions(content_type="script")``。
"""
from __future__ import annotations

from typing import Any

SCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["title", "body"],
}


def build_script_prompt(
    angles: list[dict[str, Any]],
    topic_id: str | None,
    outline: str | None,
    style: str,
    brand_block: str | None = None,
) -> str:
    """构造脚本生成提示（基于选定角度 + 可选大纲）。

    Args:
        angles: 候选角度列表（来自 TopicProposal）。
        topic_id: 选定角度 id（缺省取首个）。
        outline: 用户补充大纲。
        style: 脚本风格。
        brand_block: 可选品牌画像注入块（Tranche 2，PRD-BRD-002；
            由 ``handlers.brand.format_brand_prompt_block`` 生成）。
    """
    chosen = next((a for a in angles if a.get("id") == topic_id), angles[0] if angles else {})
    angle_text = chosen.get("title", "")
    extra = f"\n用户补充大纲：{outline}" if outline else ""
    brand = f"{brand_block}\n\n" if brand_block else ""
    return (
        f"{brand}"
        f"基于选题角度「{angle_text}」，写一篇「{style}」风格的短视频口播脚本。"
        "包含标题与正文（口语化、有节奏）。以 JSON 返回，结构见 schema。"
        f"{extra}"
    )
