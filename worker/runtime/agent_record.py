"""外部 Agent 活动登记（PRD-AGT-003）。

验收标准是「所有外部结果具备来源和信任等级」。此前 ``agent_tasks`` /
``agent_artifacts`` 两张表只有只读 handler，**没有任何生产写入路径**——
MCP 发起的分析产物与用户自己点的分析完全无法区分，信任等级无从谈起。

本模块在 Command Bus 成功返回后统一登记（而不是逐个改 handler）：

- ``agent_connections``：按 ``source``（mcp / a2a / acp）确保一行，代表
  「这条协议通道」。
- ``agent_tasks``：一次外部调用一行，记录发起者、任务类型、关联项目。
- ``agent_artifacts``：每个产出 artifact 一行，带 ``trust_level`` 与
  ``review_state='pending_review'``。

信任等级取 ``external-unverified``（schemas/artifact-envelope.schema.json
的枚举）：内容由本地模型生成但**调用方是外部 Agent**，未经人工复核。
用户在 UI 复核后可升级为 ``human-reviewed``。

登记失败绝不影响业务结果（与 audit 一致：吞掉并记日志）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from worker.runtime.models import CommandEnvelope

logger = logging.getLogger("worker.runtime")

#: 外部 Agent 产出的默认信任等级（未经人工复核）
DEFAULT_TRUST_LEVEL = "external-unverified"

#: 复核状态初值（UI 复核后可升级为 human-reviewed）
DEFAULT_REVIEW_STATE = "pending_review"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def ensure_connection(conn: Any, protocol: str) -> str:
    """确保该协议的 ``agent_connections`` 行存在，返回其 id。

    连接 id 用协议名派生（``conn_mcp``），保证同协议复用同一行 ——
    ``agent_tasks.target_agent_id`` 有 FK 指向此表。
    """
    conn_id = f"conn_{protocol}"
    row = conn.execute(
        "SELECT id FROM agent_connections WHERE id=?", (conn_id,)
    ).fetchone()
    if row is not None:
        return conn_id
    now = _now()
    conn.execute(
        "INSERT INTO agent_connections "
        "(id, protocol, endpoint_or_command, local_or_remote, trust_level, "
        "auth_ref, status, capabilities, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            conn_id, protocol, f"stdio:{protocol}", "local",
            DEFAULT_TRUST_LEVEL, None, "active", "[]", now, now,
        ),
    )
    return conn_id


def record_agent_activity(
    conn: Any,
    env: CommandEnvelope,
    *,
    artifact_ids: list[str],
    ok: bool,
) -> str | None:
    """登记一次外部 Agent 调用及其产物；返回 ``agent_tasks.id``。

    ``project_id`` 为空时仍记录任务（列可空），只是产物无法归属项目 ——
    ``agent_artifacts.project_id`` 非空且有 FK，故此时跳过产物登记。
    """
    try:
        now = _now()
        connection_id = ensure_connection(conn, env.source)
        actor = env.actor or {}
        task_id = f"atask_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO agent_tasks "
            "(id, initiator, target_agent_id, session_id, project_id, task_type, "
            "input_artifact_ids, state, progress, cost, timeout_at, "
            "correlation_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                f"{actor.get('type', 'agent')}:{actor.get('id', 'unknown')}",
                connection_id,
                None,
                env.projectId,
                env.commandType,
                "[]",
                "succeeded" if ok else "failed",
                1.0 if ok else 0.0,
                None,
                None,
                env.commandId,
                now,
                now,
            ),
        )

        if ok and env.projectId:
            for artifact_id in artifact_ids:
                conn.execute(
                    "INSERT INTO agent_artifacts "
                    "(id, project_id, agent_task_id, artifact_type, schema_version, "
                    "producer_agent_id, content_uri_or_json, source_refs, "
                    "trust_level, content_hash, review_state, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"aart_{uuid.uuid4().hex}",
                        env.projectId,
                        task_id,
                        env.commandType,
                        "1",
                        connection_id,
                        json.dumps({"content_version_id": artifact_id}),
                        json.dumps([artifact_id]),
                        DEFAULT_TRUST_LEVEL,
                        hashlib.sha256(artifact_id.encode("utf-8")).hexdigest(),
                        DEFAULT_REVIEW_STATE,
                        now,
                    ),
                )
        conn.commit()
        return task_id
    except Exception:  # noqa: BLE001 - 登记失败绝不影响业务结果
        logger.exception("agent activity record failed command=%s", env.commandType)
        return None
