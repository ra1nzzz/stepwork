"""内容分析 JSON Schema（W4，Batch 2）。

这是发送给 AI Provider 的 ``response_format.json_schema``，
也是前端 / 工具消费分析结果的契约。``analysis/report.py`` 中的
``AnalysisReport`` pydantic 模型与之保持字段一致（见 test_analysis
的 schema 一致性断言）。

落盘文件：``schemas/analysis.schema.json``（由本 dict 生成，二者同步）。
"""

from __future__ import annotations

from typing import Any

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "AnalysisReport",
    "properties": {
        "summary": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "sentiment": {
            "type": "string",
            "enum": ["positive", "neutral", "negative"],
        },
        "suggested_title": {"type": ["string", "null"]},
        "suggested_tags": {"type": "array", "items": {"type": "string"}},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "target_audience": {"type": ["string", "null"]},
        "provider": {"type": "string"},
        "model": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "summary",
        "topics",
        "sentiment",
        "suggested_title",
        "suggested_tags",
        "key_points",
        "target_audience",
        "provider",
        "model",
        "confidence",
    ],
}
