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
                },
                "required": ["id", "title", "rationale", "hook"],
            },
        }
    },
    "required": ["angles"],
}


def build_topic_prompt(source_text: str, count: int) -> str:
    """构造差异化角度生成提示（取素材前 2000 字）。"""
    excerpt = source_text[:2000]
    return (
        f"基于以下素材，提出 {count} 个差异化的短视频选题角度。"
        "每个角度需有清晰标题、差异化依据、以及能抓住注意力的开头钩子。\n\n"
        f"素材：\n{excerpt}\n\n以 JSON 返回，结构见 schema。"
    )
