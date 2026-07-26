"""内容相似度（PRD-SCR-004 历史选题重复提醒 / PRD-SCR-005 原创性提醒）。

设置页的 ``check-similarity`` 此前是个**空开关**：UI 能勾选，背后零计算。
本模块提供实际算法，纯 stdlib（无 numpy/embedding 依赖，离线可用、确定）。

算法：字符级 **3-gram Jaccard**。选它而不是 embedding 的理由：

- 完全本地、零依赖、确定可复现，符合「本地优先」；
- 中文没有天然词边界，字符 n-gram 比分词更稳；
- 目标是「提示相似」而非语义判定 —— PRD-SCR-005 明确要求
  「不做法律结论」，所以给的是**提醒**，不是判定。

阈值取 0.5（经验值，可由调用方覆盖）：高于它才提示，避免噪声。
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

#: n-gram 长度（中文 3 字、英文 3 字符）
_NGRAM = 3

#: 默认提示阈值：Jaccard 相似度高于此值才认为「值得提醒」
DEFAULT_THRESHOLD = 0.5

#: 只保留中日韩文字、字母、数字；标点/空白不参与比较
_KEEP_RE = re.compile(r"[^\w一-鿿]+", re.UNICODE)


class SimilarityHit(NamedTuple):
    """一条相似度命中（供 UI 提示，绝不是法律结论）。"""

    ref_id: str
    score: float
    label: str


def normalize(text: str) -> str:
    """归一化：转小写、去标点与空白（避免排版差异干扰比较）。"""
    return _KEEP_RE.sub("", (text or "").lower())


def ngrams(text: str, n: int = _NGRAM) -> set[str]:
    """取字符级 n-gram 集合；文本短于 n 时退化为整串。"""
    norm = normalize(text)
    if not norm:
        return set()
    if len(norm) <= n:
        return {norm}
    return {norm[i : i + n] for i in range(len(norm) - n + 1)}


def jaccard(a: str, b: str) -> float:
    """两段文本的字符 3-gram Jaccard 相似度（0.0–1.0）。"""
    sa, sb = ngrams(a), ngrams(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def find_similar(
    text: str,
    candidates: list[dict[str, Any]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int = 5,
) -> list[SimilarityHit]:
    """在候选中找出与 ``text`` 相似度超过阈值的条目（按分数降序）。

    Args:
        text: 待检文本。
        candidates: 每项形如 ``{"id":…, "text":…, "label":…}``。
        threshold: 提示阈值。
        limit: 最多返回条数。
    """
    hits: list[SimilarityHit] = []
    for cand in candidates:
        score = jaccard(text, str(cand.get("text") or ""))
        if score >= threshold:
            hits.append(
                SimilarityHit(
                    ref_id=str(cand.get("id") or ""),
                    score=round(score, 4),
                    label=str(cand.get("label") or ""),
                )
            )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def hits_to_warnings(hits: list[SimilarityHit], kind: str) -> list[dict[str, Any]]:
    """把命中转为 ``detail`` 中的告警对象（措辞保持「提醒」而非判定）。"""
    return [
        {
            "kind": kind,
            "ref_id": h.ref_id,
            "score": h.score,
            "label": h.label,
            "message": (
                f"与既有内容「{h.label or h.ref_id[:8]}」相似度约 "
                f"{h.score:.0%}，请确认是否重复或需要调整表达。"
            ),
        }
        for h in hits
    ]
