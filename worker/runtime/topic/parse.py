"""TopicProposal 解析（W5）。"""
from __future__ import annotations

from typing import Any

from worker.runtime.models import TopicAngle, TopicProposal
from worker.runtime.providers.ai.base import parse_json_response


def parse_topic_proposal(
    raw: dict[str, Any] | str, count: int
) -> TopicProposal:
    """从模型返回解析为 ``TopicProposal``（校验 + 截断到 ``count``）。"""
    data: dict[str, Any] = raw if isinstance(raw, dict) else parse_json_response(raw)
    angles_raw = data.get("angles") or []
    angles: list[TopicAngle] = []
    for i, a in enumerate(angles_raw[: max(count, 1)]):
        angles.append(
            TopicAngle(
                id=str(a.get("id") or f"angle-{i + 1}"),
                title=str(a.get("title", "")),
                rationale=str(a.get("rationale", "")),
                hook=str(a.get("hook", "")),
            )
        )
    if not angles:
        raise ValueError("model returned no topic angles")
    return TopicProposal(angles=angles)
