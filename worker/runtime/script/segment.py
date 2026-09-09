"""脚本正文 → 分幕切分（S2 生产端）。

**为什么需要它**：``video_scenes`` 表建好了、读写层也接进了命令总线，但如果
分幕只能靠外部手工调 ``SaveVideoScenes`` 灌进来，那「幕」永远是孤岛 ——
``GenerateScript`` 出完文案，下游配音/配图/渲染依旧拿不到幕，整条流水线
还是断的。本模块把「文案 → 幕」变成**确定性的纯函数**，每次脚本落版自动派生。

**为什么是确定性切分，而不是再问一次模型**：

- **零额外调用、零新失败模式**：不依赖模型遵守新 schema，离线能跑、能测。
  让模型吐 ``scenes`` 数组看着更聪明，但模型不遵守时只能静默退化 —— 而这
  正是本项目反复踩过的坑（静默失效比报错贵得多）。
- **幕的边界信息本来就在正文里**：一条幕 ≈ 一口气 ≈ 一个镜头，中文口播里
  句读已经把这条边界写出来了。
- 模型真正该干的活（``emotion`` / ``highlight``）等有了**真实消费方**再加。
  现在写进去就是没人读、也没人校验的死字段。

**切分规则**（纯函数；与段落级编辑共用同一套段落规则，否则索引会错位）：

1. 先按空行切段（复用 :func:`~worker.runtime.script.paragraph.split_paragraphs`）
   —— 作者敲下的空行是有意的边界；
2. 段长不超过 ``max_chars`` → 整段成一幕；
3. 超长的段先按句读切成句，再贪心打包到不超过 ``max_chars``。单句超长时
   **整句成幕，绝不在句子中间劈开** —— 幕是 TTS 的最小调用单位，劈开就是断字；
4. ``max_scenes`` 是硬上限，防正文异常时灌出上千幕。
"""
from __future__ import annotations

import json
import re
from typing import Any

from worker.runtime.script.paragraph import split_paragraphs

#: 单幕字数软上限。中文口播约 4–5 字/秒，36 字 ≈ 7–9 秒 ≈ 一到两个镜头。
DEFAULT_MAX_CHARS = 36

#: 幕数硬上限（安全阀，不是业务目标）。
DEFAULT_MAX_SCENES = 80

#: 句读分隔符。刻意**不含** ASCII 句点：``3.5`` / ``AI.`` 不该被切开。
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?；;…\n])")

#: 判断一个片段是否「只剩标点」用（``……`` 会被切成两个 ``…``，不能各成一幕）
_TERMINATORS = "。！？!?；;…"

#: TipTap/ProseMirror 里的块级节点：块之间补空行，才切得出段落
_BLOCK_TYPES = frozenset(
    {
        "paragraph",
        "heading",
        "blockquote",
        "bulletList",
        "orderedList",
        "listItem",
        "codeBlock",
    }
)


def _is_block(node: Any) -> bool:
    return isinstance(node, dict) and str(node.get("type", "")) in _BLOCK_TYPES


def _walk(node: Any) -> str:
    """递归摊平 TipTap/ProseMirror 文档树为纯文本。"""
    if isinstance(node, list):
        return "".join(("\n\n" + _walk(v)) if _is_block(v) else _walk(v) for v in node)
    if isinstance(node, dict):
        parts: list[str] = []
        text = node.get("text")
        if isinstance(text, str):
            parts.append(text)
        content = node.get("content")
        if isinstance(content, (list, dict)):
            parts.append(_walk(content))
        return "".join(parts)
    return ""


def plain_text(content: Any) -> str:
    """把脚本内容归一成纯文本。

    三种输入形态都要能吃下，否则编辑器路径（TipTap JSON）派生不出幕：

    - 纯文本 → 原样返回；
    - ``{"title","body"}`` JSON（``GenerateScript`` 产出）→ 取 ``body``；
    - TipTap/ProseMirror JSON → 递归摊平，块级节点之间补空行。
    """
    if isinstance(content, str):
        stripped = content.strip()
        if stripped[:1] in ("{", "["):
            try:
                return plain_text(json.loads(stripped))
            except (ValueError, TypeError):
                return content
        return content
    if isinstance(content, dict):
        body = content.get("body")
        if isinstance(body, str):
            return body
        return _walk(content)
    if isinstance(content, list):
        return _walk(content)
    return "" if content is None else str(content)


def split_sentences(text: str) -> list[str]:
    """按句读切句（保留标点，丢弃只剩标点的空片段）。"""
    out: list[str] = []
    for piece in _SENT_SPLIT_RE.split(text or ""):
        s = piece.strip()
        if s and s.strip(_TERMINATORS):
            out.append(s)
    return out


def _pack(sentences: list[str], max_chars: int) -> list[str]:
    """贪心打包：相邻短句合成一幕，单句超长则独占一幕。"""
    out: list[str] = []
    buf = ""
    for s in sentences:
        if buf and len(buf) + len(s) > max_chars:
            out.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        out.append(buf)
    return out


def segment_scenes(
    body: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_scenes: int = DEFAULT_MAX_SCENES,
) -> list[str]:
    """脚本正文 → 幕文本列表（按幕序）。

    Args:
        body: 纯文本正文（TipTap JSON 请先过 :func:`plain_text`）。
        max_chars: 单幕字数软上限。
        max_scenes: 幕数硬上限，超出即截断。

    Returns:
        幕文本列表；空正文 → 空列表。``seq`` 由调用方按下标赋予。
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if max_scenes <= 0:
        raise ValueError("max_scenes must be positive")

    scenes: list[str] = []
    for para in split_paragraphs(body or ""):
        if len(para) <= max_chars:
            scenes.append(para)
            continue
        scenes.extend(_pack(split_sentences(para), max_chars))
        if len(scenes) >= max_scenes:
            break
    return scenes[:max_scenes]
