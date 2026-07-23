"""Provenance 只读聚合 handler（W8 L.30）。

路由 ``GetProvenance`` 命令，best-effort 聚合某主体的来源链路：

1. 优先查 ``provenance_records`` 表（W9 统一写入路径落库的正式记录）。
2. 若无记录，回退查 ``content_versions.producer`` 字段（W3-W7 各 AI handler
   写入的 proto-provenance，JSON 字符串），转译为统一形状。
3. 都没有则返 ``NOT_FOUND``。

W8 只读聚合，**不写库**；统一写入路径推 W9（SYSTEM_SPEC §8.3）。
"""

from __future__ import annotations

import json
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult

# proto-provenance producer.kind 中标识「AI 生成」的前缀；
# 命中 ``ai-analysis`` / ``ai-topic`` / ``ai-script`` 三种形状时，
# 回退聚合把 ai_label_state 标为 "ai-generated"。
_AI_KIND_PREFIX = "ai-"


def _parse_json_list(raw: str) -> list[Any]:
    """安全解析 ``provenance_records`` 的 JSON 数组列。

    列默认值为 ``'[]'``，但防御性解析：任何异常（含非法 JSON / 类型不符）
    都返空列表，绝不击垮只读聚合。
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return parsed


def _record_row_to_provenance(row: Any) -> dict[str, Any]:
    """把 ``provenance_records`` 行转为统一 provenance dict（JSON 列解析）。"""
    return {
        "subject_type": str(row["subject_type"]),
        "subject_id": str(row["subject_id"]),
        "source_ids": _parse_json_list(str(row["source_ids"])),
        "model_calls": _parse_json_list(str(row["model_calls"])),
        "agent_tasks": _parse_json_list(str(row["agent_tasks"])),
        "plugin_executions": _parse_json_list(str(row["plugin_executions"])),
        "user_edits": _parse_json_list(str(row["user_edits"])),
        "ai_label_state": str(row["ai_label_state"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _producer_to_provenance(producer_raw: str) -> dict[str, Any]:
    """把 ``content_versions.producer`` 转译为统一 provenance 形状。

    识别三种 proto-provenance 形状（``ai-analysis`` / ``ai-topic`` /
    ``ai-script``），统一映射为：

    ::

        {model_calls: [{"provider":..., "model":..., "kind":...}],
         source_ids: [], agent_tasks: [], plugin_executions: [],
         user_edits: [], ai_label_state: "ai-generated"}

    非法 JSON / 非 dict / 非 ``ai-*`` kind → ``ai_label_state="unknown"``、
    各列表为空（W8 只覆盖三种 AI 形状，其余推 W9 统一写入路径）。
    """
    base: dict[str, Any] = {
        "source_ids": [],
        "model_calls": [],
        "agent_tasks": [],
        "plugin_executions": [],
        "user_edits": [],
        "ai_label_state": "unknown",
    }
    if not producer_raw:
        return base
    try:
        producer = json.loads(producer_raw)
    except (json.JSONDecodeError, TypeError):
        return base
    if not isinstance(producer, dict):
        return base
    kind = producer.get("kind")
    if not isinstance(kind, str) or not kind.startswith(_AI_KIND_PREFIX):
        return base
    base["model_calls"] = [
        {
            "provider": producer.get("provider"),
            "model": producer.get("model"),
            "kind": kind,
        }
    ]
    base["ai_label_state"] = "ai-generated"
    return base


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由 ``GetProvenance``：best-effort 聚合主体来源链路（只读）。"""
    payload = env.payload or {}
    subject_type = payload.get("subjectType") or payload.get("subject_type")
    subject_id = payload.get("subjectId") or payload.get("subject_id")
    if not subject_type or not subject_id:
        raise DispatchError("INVALID_ARGUMENT", "missing subjectType/subjectId")

    conn = deps.repos.conn

    # 1. 优先查 provenance_records（W9 统一写入路径落库的正式记录）
    row = conn.execute(
        "SELECT * FROM provenance_records WHERE subject_type=? AND subject_id=?",
        (subject_type, subject_id),
    ).fetchone()
    if row is not None:
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"provenance": _record_row_to_provenance(row)},
        )

    # 2. 回退查 content_versions.producer（proto-provenance）
    #    subject_id 视作 content_version id；best-effort，无 FK 强约束亦接受。
    cv_row = conn.execute(
        "SELECT producer FROM content_versions WHERE id=?",
        (subject_id,),
    ).fetchone()
    if cv_row is not None:
        producer_raw = (
            str(cv_row["producer"]) if cv_row["producer"] is not None else ""
        )
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"provenance": _producer_to_provenance(producer_raw)},
        )

    # 3. 都没有 → NOT_FOUND
    raise DispatchError(
        "NOT_FOUND",
        f"provenance for {subject_type}/{subject_id!r} not found",
    )
