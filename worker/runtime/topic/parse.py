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
        # PRD-SCR-001：受众/观点/风险与其余字段一样必须落库；模型漏给时
        # 保持 None / 空列表，绝不静默丢弃已给出的值。
        risks_raw = a.get("risks")
        risks = (
            [str(r) for r in risks_raw if str(r).strip()]
            if isinstance(risks_raw, list)
            else []
        )
        angles.append(
            TopicAngle(
                id=str(a.get("id") or f"angle-{i + 1}"),
                title=str(a.get("title", "")),
                rationale=str(a.get("rationale", "")),
                hook=str(a.get("hook", "")),
                audience=str(a["audience"]) if a.get("audience") else None,
                stance=str(a["stance"]) if a.get("stance") else None,
                risks=risks,
            )
        )
    if not angles:
        raise ValueError("model returned no topic angles")
    return TopicProposal(angles=angles)
