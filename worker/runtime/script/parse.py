"""Script 解析（W5）。"""
from __future__ import annotations

from typing import Any

from worker.runtime.providers.ai.base import parse_json_response


def parse_script(raw: dict[str, Any] | str) -> dict[str, Any]:
    """从模型返回解析为脚本内容 dict（``title`` / ``body``）。"""
    data: dict[str, Any] = raw if isinstance(raw, dict) else parse_json_response(raw)
    return {
        "title": str(data.get("title", "")),
        "body": str(data.get("body", "")),
    }
