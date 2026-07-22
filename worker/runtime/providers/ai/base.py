"""AI Provider 协议与公共辅助（W4，Batch 2）。

``AIProvider`` 为结构化协议（PEP 544，``runtime_checkable``）。
实现（cloud / openai-compatible / ollama）只需满足
``name`` / ``model`` / ``estimated_cost_per_1k`` / ``complete`` 即可。
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AIProvider(Protocol):
    """内容分析 Provider 协议。"""

    name: str
    model: str
    estimated_cost_per_1k: float

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """向模型请求一次补全。

        Args:
            prompt: 完整提示词。
            schema: 可选 JSON Schema，用于约束结构化输出（``response_format``）。

        Returns:
            模型返回的结构化 ``dict``。
        """
        ...


def parse_json_response(text: str) -> dict[str, Any]:
    """解析模型文本为 dict，容忍 ```json 围栏包裹。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
        t = t.strip()
    data: dict[str, Any] = json.loads(t)
    return data
