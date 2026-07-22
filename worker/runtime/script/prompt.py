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
) -> str:
    """构造脚本生成提示（基于选定角度 + 可选大纲）。"""
    chosen = next((a for a in angles if a.get("id") == topic_id), angles[0] if angles else {})
    angle_text = chosen.get("title", "")
    extra = f"\n用户补充大纲：{outline}" if outline else ""
    return (
        f"基于选题角度「{angle_text}」，写一篇「{style}」风格的短视频口播脚本。"
        "包含标题与正文（口语化、有节奏）。以 JSON 返回，结构见 schema。"
        f"{extra}"
    )
