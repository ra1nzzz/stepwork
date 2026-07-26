"""内容相似度（PRD-SCR-004 历史选题重复提醒 / PRD-SCR-005 原创性提醒）。

设置页 ``brand.mustExecute`` 里的 ``check-similarity`` 此前是个**空开关**：
UI 能勾选，背后零计算。本模块提供实际算法（纯 stdlib，无 numpy/embedding
依赖，离线可用、确定），并由 :func:`similarity_check_enabled` 真正消费
那个开关 —— 勾掉它就不再检索历史、不再产出提醒。

算法：字符级 **3-gram Jaccard**。选它而不是 embedding 的理由：

- 完全本地、零依赖、确定可复现，符合「本地优先」；
- 中文没有天然词边界，字符 n-gram 比分词更稳；
- 目标是「提示相似」而非语义判定 —— PRD-SCR-005 明确要求
  「不做法律结论」，所以给的是**提醒**，不是判定。

阈值分两档：短文本（选题）0.5、长文本（整篇脚本）0.28 —— 见常量注释。
"""

from __future__ import annotations

import json
import re
from typing import Any, NamedTuple

#: n-gram 长度（中文 3 字、英文 3 字符）
_NGRAM = 3

#: 短文本（选题标题+依据）提示阈值：高度重合才提醒，避免噪声
DEFAULT_THRESHOLD = 0.5

#: 长文本（整篇脚本）阈值，配合 :func:`containment` 使用。
#:
#: 原创性提醒的目标是**检测复用**（整段搬运），不是检测同题材 —— 同题材
#: 不同表达属于正常创作，报警只会变成噪声。整篇 Jaccard 两头不讨好：
#: 实测「整段抄用」只有 0.767（因两篇长度不同而被稀释），「同题材改写」
#: 只有 0.093，找不到能同时满足两端的阈值。改用 containment 后实测：
#: 整段抄用 1.000 / 完全相同 1.000 / 同题材改写 0.174 / 不同题材 0.000，
#: 0.6 能干净分离。
SCRIPT_THRESHOLD = 0.6

#: 设置页 brand.mustExecute 里代表「检查历史内容相似度」的标记
SIMILARITY_FLAG = "check-similarity"

#: 只保留中日韩文字、字母、数字；标点/空白不参与比较
_KEEP_RE = re.compile(r"[^\w一-鿿]+", re.UNICODE)


class SimilarityHit(NamedTuple):
    """一条相似度命中（供 UI 提示，绝不是法律结论）。"""

    ref_id: str
    score: float
    label: str


def similarity_check_enabled(conn: Any, workspace_id: str) -> bool:
    """读取设置页开关：是否执行相似度检查（PRD-SCR-005）。

    ``brand.mustExecute`` 里含 ``check-similarity`` 才检查。设置缺失时
    **默认开启** —— 与 ``handlers.config.DEFAULT_CONFIG`` 的默认值一致，
    也避免旧工作区（settings 为空）悄悄失去这项保护。
    """
    try:
        row = conn.execute(
            "SELECT settings FROM workspaces WHERE id=?", (workspace_id,)
        ).fetchone()
    except Exception:  # noqa: BLE001 - 读设置失败时按默认（开启）处理
        return True
    if row is None or not row["settings"]:
        return True
    try:
        settings = json.loads(row["settings"])
    except (TypeError, ValueError):
        return True
    brand = settings.get("brand") if isinstance(settings, dict) else None
    if not isinstance(brand, dict) or "mustExecute" not in brand:
        return True
    must = brand.get("mustExecute")
    return isinstance(must, list) and SIMILARITY_FLAG in must


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


def containment(a: str, b: str) -> float:
    """重叠 n-gram 占**较小**集合的比例（0.0–1.0）。

    查重用它而非 Jaccard：一段被整体搬进另一篇长稿时，Jaccard 会被长度
    差稀释（实测只有 0.767），containment 仍是 1.0。这正是「原创性提醒」
    要抓的情形。
    """
    sa, sb = ngrams(a), ngrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def find_similar(
    text: str,
    candidates: list[dict[str, Any]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int = 5,
    metric: str = "jaccard",
) -> list[SimilarityHit]:
    """在候选中找出与 ``text`` 相似度超过阈值的条目（按分数降序）。

    Args:
        text: 待检文本。
        candidates: 每项形如 ``{"id":…, "text":…, "label":…}``。
        threshold: 提示阈值。
        limit: 最多返回条数。
        metric: ``jaccard``（默认，短文本）或 ``containment``（长文本查重）。
    """
    score_fn = containment if metric == "containment" else jaccard
    hits: list[SimilarityHit] = []
    for cand in candidates:
        score = score_fn(text, str(cand.get("text") or ""))
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
