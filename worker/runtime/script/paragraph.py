"""段落级编辑（PRD-SCR-003：段落级生成、重写、扩写和压缩）。

契约要点：

- 段落切分是**纯函数**（:func:`split_paragraphs` / :func:`join_paragraphs`），
  前后端必须用同一套规则，否则 ``paragraph_index`` 会对不上。规则：按空行
  切块，块内换行保留（口播脚本常见「一段多行」）。
- 每次操作只替换目标段落，其余段落原样保留 —— 编辑器里没被选中的内容
  绝不能被模型改写。
- 结果落成**新的 ContentVersion**（parent 指向原版本），因此「所有操作
  可撤销」= 回滚到 parent 版本，与既有版本链一致。
"""

from __future__ import annotations

import re
from typing import Any

# 段落分隔：一个或多个空行（允许行尾空白）
_PARA_SPLIT_RE = re.compile(r"\n\s*\n+")

#: 段落级操作及其对模型的指令
OPERATIONS: dict[str, str] = {
    "rewrite": "在保持原意与信息量的前提下重写这一段，让表达更清晰自然。",
    "expand": "扩写这一段，补充细节、例子或过渡，使其更充实（约为原长的 1.5–2 倍）。",
    "condense": "压缩这一段，保留全部关键信息，去掉冗余（约为原长的一半）。",
    "generate": "在这一段的位置续写/生成新的一段，与上下文自然衔接。",
}

PARAGRAPH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def split_paragraphs(text: str) -> list[str]:
    """按空行把正文切成段落（去除首尾空白，丢弃空段）。"""
    parts = [p.strip() for p in _PARA_SPLIT_RE.split(text or "")]
    return [p for p in parts if p]


def join_paragraphs(paragraphs: list[str]) -> str:
    """段落列表 → 正文（空行分隔，与 split 互为逆运算）。"""
    return "\n\n".join(p.strip() for p in paragraphs if p.strip())


def replace_paragraph(text: str, index: int, replacement: str) -> str:
    """替换第 ``index`` 段并返回新正文。

    ``replacement`` 为空串时表示删除该段。索引越界由调用方（handler）
    提前校验，此处再兜一层 ``IndexError``。
    """
    paragraphs = split_paragraphs(text)
    if index < 0 or index >= len(paragraphs):
        raise IndexError(f"paragraph index {index} out of range (0..{len(paragraphs) - 1})")
    if replacement.strip():
        paragraphs[index] = replacement.strip()
    else:
        del paragraphs[index]
    return join_paragraphs(paragraphs)


def build_paragraph_prompt(
    operation: str,
    target: str,
    *,
    before: str | None = None,
    after: str | None = None,
    instruction: str | None = None,
    brand_block: str | None = None,
) -> str:
    """构造段落级操作提示。

    附上下文段落（before/after）让改写与前后文衔接；明确要求**只输出这一段**，
    避免模型顺手把整篇重写了。
    """
    task = OPERATIONS[operation]
    brand = f"{brand_block}\n\n" if brand_block else ""
    ctx = ""
    if before:
        ctx += f"\n【上一段】\n{before}\n"
    if after:
        ctx += f"\n【下一段】\n{after}\n"
    extra = f"\n补充要求：{instruction}\n" if instruction else ""
    return (
        f"{brand}"
        f"你在编辑一篇短视频口播脚本的某一个段落。\n"
        f"任务：{task}\n"
        f"{ctx}"
        f"\n【待处理段落】\n{target}\n"
        f"{extra}"
        "\n只输出处理后的**这一段**文本，不要输出标题、编号、上下段或任何解释。"
        "以 JSON 返回，结构见 schema（字段 text）。"
    )


def parse_paragraph_result(raw: dict[str, Any] | str) -> str:
    """从模型返回中取出段落文本（容忍纯字符串返回）。"""
    if isinstance(raw, str):
        return raw.strip()
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("model returned no paragraph text")
    return text.strip()
