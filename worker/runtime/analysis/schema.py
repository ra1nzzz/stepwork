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
        "hook": {"type": ["string", "null"]},
        "structure": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "provider": {"type": "string"},
        "model": {"type": "string"},
        "confidence": {"type": "number"},
        # PRD-ANA-005「分析引用对应来源位置」：关键结论带来源锚点，
        # 使 UI 能跳转到对应时间戳或逐字稿段落。
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # 该引用支撑的结论文本（与 key_points/summary 里的措辞对应）
                    "claim": {"type": "string"},
                    # 来源时间戳（秒）；纯文本分析时为 null
                    "start_sec": {"type": ["number", "null"]},
                    # 精确模式下的场景序号；快速模式为 null
                    "scene_index": {"type": ["integer", "null"]},
                    # 逐字稿原文片段（供无时间戳时定位）
                    "quote": {"type": ["string", "null"]},
                },
                "required": ["claim", "start_sec", "scene_index", "quote"],
            },
        },
    },
    "required": [
        "summary",
        "topics",
        "sentiment",
        "suggested_title",
        "suggested_tags",
        "key_points",
        "target_audience",
        "hook",
        "structure",
        "risks",
        "provider",
        "model",
        "confidence",
        "citations",
    ],
}
