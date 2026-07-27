"""版本差异比较（PRD-SCR-006「可比较 AI 初稿和最终稿」）。

版本链此前已完备（每次编辑生成新版本、parent 串链、可回滚），但**没有
比较能力**：用户只能各自打开两个版本肉眼比对，也没有「AI 初稿」这个锚点
（需沿 parent 链自行找 ``kind=='ai-script'`` 的那一版）。

本模块用标准库 ``difflib`` 做行级差异（零依赖、确定），并提供
:func:`find_ai_draft` 沿版本链定位 AI 初稿。
"""

from __future__ import annotations

import difflib
import json
from typing import Any, Literal

#: 差异行类型
OpKind = Literal["equal", "insert", "delete"]

#: 单次比较最多返回的差异行数（防止超长脚本把响应撑爆）
_MAX_DIFF_LINES = 2000


def extract_text(content: str) -> tuple[str, str]:
    """脚本内容 → ``(正文, 标题)``。

    兼容三种落库形态：``{"title","body"}``（GenerateScript）、
    ``{"text","title"}``（编辑器保存）、裸文本。与
    ``script/history.py`` 保持同一套解析规则。
    """
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return content, ""
    if not isinstance(parsed, dict):
        return content, ""
    for key in ("body", "text"):
        value = parsed.get(key)
        if isinstance(value, str):
            return value, str(parsed.get("title") or "")
    return content, str(parsed.get("title") or "")


def diff_lines(before: str, after: str) -> list[dict[str, Any]]:
    """行级差异。

    返回 ``[{op, text, before_line, after_line}]``；``op`` 取
    equal / insert / delete。用 ``difflib.SequenceMatcher`` 而非
    ``unified_diff``，因为前端要按行渲染高亮，而不是展示 patch 文本。
    """
    old_lines = (before or "").splitlines()
    new_lines = (after or "").splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)

    result: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, line in enumerate(old_lines[i1:i2]):
                result.append(
                    {
                        "op": "equal",
                        "text": line,
                        "before_line": i1 + offset + 1,
                        "after_line": j1 + offset + 1,
                    }
                )
        else:
            # replace 拆成 delete + insert，前端只需处理三种 op
            if tag in ("replace", "delete"):
                for offset, line in enumerate(old_lines[i1:i2]):
                    result.append(
                        {
                            "op": "delete",
                            "text": line,
                            "before_line": i1 + offset + 1,
                            "after_line": None,
                        }
                    )
            if tag in ("replace", "insert"):
                for offset, line in enumerate(new_lines[j1:j2]):
                    result.append(
                        {
                            "op": "insert",
                            "text": line,
                            "before_line": None,
                            "after_line": j1 + offset + 1,
                        }
                    )
        if len(result) >= _MAX_DIFF_LINES:
            break
    return result[:_MAX_DIFF_LINES]


def summarize(lines: list[dict[str, Any]]) -> dict[str, int]:
    """差异统计（新增/删除/未变行数），供 UI 一眼看出改动量。"""
    return {
        "added": sum(1 for line in lines if line["op"] == "insert"),
        "removed": sum(1 for line in lines if line["op"] == "delete"),
        "unchanged": sum(1 for line in lines if line["op"] == "equal"),
    }


#: 视为「AI 初稿」的 producer.kind。段落级改写（ai-paragraph-edit）产生的
#: 是中间版本，不算初稿 —— 「初稿 vs 最终稿」里的初稿指 GenerateScript 那一版。
_AI_DRAFT_KIND = "ai-script"


def find_ai_draft(conn: Any, version_id: str) -> str | None:
    """沿 parent 链上溯，找到最近的 AI 初稿版本 id（PRD-SCR-006 的锚点）。

    「初稿 vs 最终稿」里的初稿指 ``GenerateScript`` 产出的那一版
    （``kind='ai-script'``）；段落级改写产生的中间版本不算初稿。
    找不到（例如全程手写）返回 ``None``。
    """
    seen: set[str] = set()
    current: str | None = version_id
    while current and current not in seen:
        seen.add(current)
        row = conn.execute(
            "SELECT id, parent_version_id, producer FROM content_versions WHERE id=?",
            (current,),
        ).fetchone()
        if row is None:
            return None
        try:
            producer = json.loads(row["producer"]) if row["producer"] else {}
        except (TypeError, ValueError):
            producer = {}
        if isinstance(producer, dict) and producer.get("kind") == _AI_DRAFT_KIND:
            return str(row["id"])
        current = row["parent_version_id"]
    return None
