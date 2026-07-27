"""分析产物模型（W4，Batch 2）。

``AnalysisReport`` 为 canonical 校验器；``parse_analysis_report``
即为"对照 analysis.schema.json 校验"的入口（pydantic 校验
保证结构与 schema 一致）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """一条来源引用（PRD-ANA-005：关键结论可跳转时间戳或文本段落）。"""

    claim: str = ""
    #: 来源时间戳（秒）；纯文本分析无时间信息时为 None
    start_sec: float | None = None
    #: 精确模式下的场景序号（对应 producer.scenes）；快速模式为 None
    scene_index: int | None = None
    #: 逐字稿原文片段（无时间戳时用于文本定位）
    quote: str | None = None


class AnalysisReport(BaseModel):
    """一次内容分析的结构化结果。"""

    summary: str
    topics: list[str] = Field(default_factory=list)
    sentiment: Literal["positive", "neutral", "negative"]
    suggested_title: str | None = None
    suggested_tags: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    target_audience: str | None = None
    # Tranche 2（PRD-ANA-002/004）：开头钩子 / 内容结构骨架 / 风险点。
    # pydantic 侧给默认值，兼容旧版本落库内容（缺字段时不炸回读）。
    hook: str | None = None
    structure: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    confidence: float = 0.0
    # PRD-ANA-005：关键结论的来源锚点。给默认值以兼容旧版本落库内容。
    citations: list[Citation] = Field(default_factory=list)


def parse_analysis_report(data: dict[str, Any]) -> AnalysisReport:
    """解析并校验分析 dict（对照 analysis.schema.json）。

    Raises:
        pydantic.ValidationError: 结构不合法。
    """
    return AnalysisReport.model_validate(data)
