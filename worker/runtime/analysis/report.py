"""分析产物模型（W4，Batch 2）。

``AnalysisReport`` 为 canonical 校验器；``parse_analysis_report``
即为"对照 analysis.schema.json 校验"的入口（pydantic 校验
保证结构与 schema 一致）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AnalysisReport(BaseModel):
    """一次内容分析的结构化结果。"""

    summary: str
    topics: list[str] = Field(default_factory=list)
    sentiment: Literal["positive", "neutral", "negative"]
    suggested_title: str | None = None
    suggested_tags: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    target_audience: str | None = None
    provider: str = ""
    model: str = ""
    confidence: float = 0.0


def parse_analysis_report(data: dict[str, Any]) -> AnalysisReport:
    """解析并校验分析 dict（对照 analysis.schema.json）。

    Raises:
        pydantic.ValidationError: 结构不合法。
    """
    return AnalysisReport.model_validate(data)
