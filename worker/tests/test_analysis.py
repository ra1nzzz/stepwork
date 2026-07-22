"""Batch 2：分析 prompt / report / schema 一致性测试。"""

from __future__ import annotations

from typing import Any

from worker.runtime.analysis.prompt import build_analysis_prompt
from worker.runtime.analysis.report import AnalysisReport, parse_analysis_report
from worker.runtime.analysis.schema import ANALYSIS_SCHEMA

_VALID: dict[str, Any] = {
    "summary": "本期聊自动化工作流。",
    "topics": ["自动化"],
    "sentiment": "positive",
    "suggested_title": "自动化工作流入门",
    "suggested_tags": ["自动化"],
    "key_points": ["导入", "转写"],
    "target_audience": "创作者",
    "provider": "mock",
    "model": "mock-1",
    "confidence": 0.9,
}


def test_build_prompt_includes_brand_and_text() -> None:
    prompt = build_analysis_prompt(
        {"text": "素材转写内容……"},
        {"name": "品牌A", "pillars": ["效率"]},
    )
    assert "品牌A" in prompt
    assert "素材转写内容" in prompt
    assert "summary" in prompt


def test_parse_report_valid() -> None:
    report = parse_analysis_report(_VALID)
    assert isinstance(report, AnalysisReport)
    assert report.sentiment == "positive"
    assert report.confidence == 0.9


def test_parse_report_rejects_invalid() -> None:
    bad = dict(_VALID)
    bad["sentiment"] = "happy"  # 不在 enum
    raised = False
    try:
        parse_analysis_report(bad)
    except Exception:
        raised = True
    assert raised, "expected validation error for bad sentiment"


def test_schema_matches_model_fields() -> None:
    model_fields = set(AnalysisReport.model_fields.keys())
    schema_props = set(ANALYSIS_SCHEMA["properties"].keys())
    assert model_fields == schema_props
