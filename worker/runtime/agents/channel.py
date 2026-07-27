"""出站 Agent 通道的公共行为（MCP / A2A / ACP 共用）。

三个协议 handler 各自实现了一遍几乎相同的东西：查连接 + 校验协议 + 校验
启用状态、写 ``agent_tasks`` + ``agent_artifacts`` 留痕、常量 TRUST_LEVEL /
REVIEW_STATE。这是连写三遍攒下来的 —— 第四个协议进来会变成第四遍。

抽出来的部分刻意**只覆盖三者真正相同的语义**：

- 连接的加载与校验（协议必须对得上、停用的不可调用）；
- PRD-AGT-003 的留痕（一次调用一条 task，一份结果一条 artifact，
  信任等级一律 external-unverified + pending_review）。

传输差异（stdio 短连接 / HTTP / stdio 长连接双向）留在各自 handler 里 ——
那才是三者真正不同的地方，硬抽会做出一个谁都不合身的抽象。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.db.rows import now_iso, row_to_dict
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope

#: 外部拿回的内容一律未经复核（PRD-AGT-003）。
#:
#: 这两个常量此前在 4 个文件里各写一份 —— 一旦某处改了值，UI 的复核入口就会
#: 漏掉那一类产物，而且不会有任何报错。
TRUST_LEVEL = "external-unverified"
REVIEW_STATE = "pending_review"

#: 单个结果入库的文本上限（留痕即可，不做归档）
MAX_RESULT_CHARS = 20000


def require(payload: dict[str, Any], *names: str) -> str:
    """取第一个非空的别名字段（兼容 camelCase / snake_case）。

    第一个名字用于报错文案 —— 它应当是对外文档里的那个拼法。
    """
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return str(value)
    raise DispatchError("INVALID_ARGUMENT", f"{names[0]} required")


def load_connection(deps: Deps, conn_id: str, *, protocol: str, label: str) -> dict[str, Any]:
    """按 id 取连接并校验协议与启用状态。

    Args:
        protocol: 期望的 ``agent_connections.protocol``（如 ``mcp-client``）。
        label: 报错文案里对该协议的称呼（如「出站 MCP 连接」）。

    协议校验不是形式主义：三种连接的 ``endpoint_or_command`` 语义完全不同
    （stdio 命令行 / HTTP 地址 / 本地 Agent 命令行），串用会拿命令行当 URL 去
    连，报出来的错完全看不懂。
    """
    row = deps.repos.conn.execute(
        "SELECT * FROM agent_connections WHERE id=?", (conn_id,)
    ).fetchone()
    if row is None:
        raise DispatchError("NOT_FOUND", f"connection {conn_id!r} not found")
    record = row_to_dict(row)
    if record.get("protocol") != protocol:
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"connection {conn_id!r} 不是{label}（protocol={record.get('protocol')!r}）",
        )
    # PRD-AGT-007：停用的通道不可调用
    if record.get("status") != "active":
        raise DispatchError(
            "CONNECTION_DISABLED", f"connection {conn_id!r} 已停用，请先在连接页启用"
        )
    return record


def insert_connection(
    deps: Deps,
    *,
    conn_id: str,
    protocol: str,
    endpoint: str,
    local_or_remote: str,
    capabilities: Any,
) -> None:
    """登记一条出站连接。"""
    stamp = now_iso()
    deps.repos.conn.execute(
        "INSERT INTO agent_connections "
        "(id, protocol, endpoint_or_command, local_or_remote, trust_level, "
        "auth_ref, status, capabilities, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            conn_id,
            protocol,
            endpoint,
            local_or_remote,
            TRUST_LEVEL,
            None,
            "active",
            json.dumps(capabilities, ensure_ascii=False),
            stamp,
            stamp,
        ),
    )


def sync_capabilities(
    deps: Deps, conn_id: str, entries: list[dict[str, Any]], *, key: str = "name"
) -> None:
    """把对端的能力目录写进 ``agent_capabilities``（先清后插）。

    先清后插而不是增量合并：目录反映的是**对方当前**暴露了什么，对方删掉一个
    工具后我们不该还留着它 —— 那会让用户调用一个已经不存在的能力。
    """
    db = deps.repos.conn
    db.execute("DELETE FROM agent_capabilities WHERE agent_connection_id=?", (conn_id,))
    stamp = now_iso()
    for entry in entries:
        name = str(entry.get(key) or "")
        if not name:
            continue
        db.execute(
            "INSERT INTO agent_capabilities "
            "(id, agent_connection_id, capability_key, capability_schema, enabled, created_at) "
            "VALUES (?,?,?,?,1,?)",
            (
                f"acap_{uuid.uuid4().hex}",
                conn_id,
                name,
                json.dumps(entry, ensure_ascii=False),
                stamp,
            ),
        )


def record_call(
    deps: Deps,
    env: CommandEnvelope,
    *,
    conn_id: str,
    task_type: str,
    text: str,
    ok: bool,
    project_id: str | None = None,
) -> str:
    """PRD-AGT-003 留痕：一次调用一条 task，一份结果一条 artifact。

    Args:
        task_type: 形如 ``mcp:search`` / ``a2a:script-drafting`` / ``acp:prompt``，
            前缀标明来自哪条协议通道。
        project_id: 覆盖信封里的项目（ACP 的会话绑定项目而非信封）。

    artifact 只在有项目上下文时落 —— ``agent_artifacts.project_id`` 是 NOT NULL，
    没有项目就没地方挂。失败调用同样记 task：连接页要能看出「这个通道一直在报错」。
    """
    db = deps.repos.conn
    stamp = now_iso()
    task_id = f"atask_{uuid.uuid4().hex}"
    actor = env.actor or {}
    target_project = project_id or (str(env.projectId) if env.projectId else None)
    db.execute(
        "INSERT INTO agent_tasks "
        "(id, initiator, target_agent_id, session_id, project_id, task_type, "
        "input_artifact_ids, state, progress, cost, timeout_at, "
        "correlation_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            task_id,
            f"{actor.get('type', 'desktop')}:{actor.get('id', 'ui')}",
            conn_id,
            None,
            target_project,
            task_type,
            "[]",
            "succeeded" if ok else "failed",
            1.0 if ok else 0.0,
            None,
            None,
            env.commandId,
            stamp,
            stamp,
        ),
    )
    if ok and target_project:
        db.execute(
            "INSERT INTO agent_artifacts "
            "(id, project_id, agent_task_id, artifact_type, schema_version, "
            "producer_agent_id, content_uri_or_json, source_refs, "
            "trust_level, content_hash, review_state, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"aart_{uuid.uuid4().hex}",
                target_project,
                task_id,
                task_type,
                "1",
                conn_id,
                json.dumps({"text": text, "task_type": task_type}, ensure_ascii=False),
                json.dumps([conn_id]),
                TRUST_LEVEL,
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                REVIEW_STATE,
                stamp,
            ),
        )
    db.commit()
    return task_id
