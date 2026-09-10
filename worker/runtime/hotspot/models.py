"""热点领域模型（migrations/0014）。

与上游 MCP 的 JSON 解耦：上游字段会随源变化（今天叫 ``word``，明天可能叫
``hotWord``），本仓落库与推荐一律用这里的形状，转换只发生在 :meth:`from_mcp`
一处 —— 上游改字段只改一个函数，不动下游。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: 用户对一条热点的态度
VERDICTS: tuple[str, ...] = ("adopted", "ignored", "rejected")


def _as_str(raw: Any, default: str = "") -> str:
    if raw is None:
        return default
    return str(raw)


def _as_float(raw: Any) -> float | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _as_meta(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    return {}


@dataclass(frozen=True)
class HotspotItem:
    """一条已落库/待落库的热点事实。

    Attributes:
        id: 上游稳定 id（``sha256(source,title,url)[:16]``），跨批次可去重。
        source: 源 id（``toutiao_hot`` / ``douhot`` …）。
        title: 标题（唯一必填）。
        url: 原文链接；没有则空串（不要塞 None 进 JSON）。
        summary: 上游已去标签压空白的摘要。
        published_at: 上游给的发布时间；榜单类源通常没有。
        score: 上游热度值。**跨源不可比**，只能源内比。
        meta: 源特有字段（board / parse / rank …）。
        batch_id: 抓取批次（本地）。
        discovered_at: 本仓抓取时刻。
    """

    id: str
    source: str
    title: str
    url: str = ""
    summary: str = ""
    published_at: str | None = None
    score: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    batch_id: str = ""
    discovered_at: str = ""

    @classmethod
    def from_mcp(
        cls,
        raw: dict[str, Any],
        *,
        batch_id: str = "",
        discovered_at: str = "",
        fallback_id: str = "",
    ) -> HotspotItem:
        """从上游 MCP 的条目 JSON 构造。

        ``id`` 缺失时用 ``fallback_id``（调用方按内容算的哈希）兜底 —— 上游
        理论上必给，但少一个字段就让整批数据落不了库不划算。
        """
        return cls(
            id=_as_str(raw.get("id")) or fallback_id,
            source=_as_str(raw.get("source"), "unknown"),
            title=_as_str(raw.get("title")),
            url=_as_str(raw.get("url")),
            summary=_as_str(raw.get("summary")),
            published_at=(_as_str(raw.get("publishedAt")) or None),
            score=_as_float(raw.get("score")),
            meta=_as_meta(raw.get("meta")),
            batch_id=batch_id,
            discovered_at=discovered_at,
        )

    def to_dict(self) -> dict[str, Any]:
        """出参形状（camelCase，与命令出参口径一致）。"""
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "publishedAt": self.published_at,
            "score": self.score,
            "meta": self.meta,
            "batchId": self.batch_id,
            "discoveredAt": self.discovered_at,
        }

    def to_row(self, workspace_id: str) -> tuple[Any, ...]:
        return (
            self.id,
            self.batch_id,
            workspace_id,
            self.source,
            self.title,
            self.url,
            self.summary,
            self.published_at,
            self.score,
            json.dumps(self.meta, ensure_ascii=False),
            self.discovered_at,
        )


@dataclass(frozen=True)
class ScoreBreakdown:
    """一次打分的分解。**每一项都出参**：只给总分等于不给解释。"""

    #: 时效（0-1）：越新越高；源没给时间取中值，不惩罚「榜单说不清洗」
    freshness: float
    #: 热度（0-1）：**源内分位**，不是原始 score（跨源量纲不可比）
    heat: float
    #: 品牌契合（0-1）：与画像定位/受众/语气/支柱/风格 DNA 的关键词重叠
    brand_fit: float
    #: 新颖度（0-1）：与历史选题越不像越高
    novelty: float
    #: 反馈修正（0-1）：被明确拒绝过的同类降权，采纳过的加权
    feedback: float
    #: 惩罚（0-1，正数=要扣）：命中禁用词等硬性风险
    penalty: float

    def as_dict(self) -> dict[str, float]:
        return {
            "freshness": round(self.freshness, 4),
            "heat": round(self.heat, 4),
            "brandFit": round(self.brand_fit, 4),
            "novelty": round(self.novelty, 4),
            "feedback": round(self.feedback, 4),
            "penalty": round(self.penalty, 4),
        }


@dataclass(frozen=True)
class Recommendation:
    """一条推荐结果（含「为什么」）。"""

    item: HotspotItem
    #: 0-100 的总分，用于排序
    score: float
    breakdown: ScoreBreakdown
    #: 人话理由（AI 写或规则模板写，见 ``reason_source``）
    reason: str
    #: ``"ai"`` | ``"rule"``。降级必须可见，否则 UI 会把模板句当 AI 洞见
    reason_source: str
    #: 风险提示（禁用词命中、与历史选题高度相似、源解析降级等）
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.item.to_dict(),
            "score": round(self.score, 2),
            "breakdown": self.breakdown.as_dict(),
            "reason": self.reason,
            "reasonSource": self.reason_source,
            "risks": self.risks,
        }
