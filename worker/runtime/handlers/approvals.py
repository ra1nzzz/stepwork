"""审批中心（PRD-AGT-008 / §9.1 / §9.2 / PRD-PUB-004）。

``approval_requests`` 表在 0003 迁移里就建好了，字段完整覆盖 §9.2 要求的
展示要素，但此前**全仓零写入、零读取、零 UI** —— §9 整章形同虚设。

本模块补齐三件事：

1. ``CreateApprovalRequest``：把「外部 Agent 想做但不被允许直接执行」的
   操作降级为**准备任务**。这正是 §9.1 的原文语义（「以下操作默认只能
   创建准备任务」）；此前 agent 撞到禁令只是被拒，既不能做也无法申请，
   属于「无路可走」而非 PRD 描述的降级。
2. ``ListApprovalRequests``：审批中心列表（§9.2 的七要素随行返回）。
3. ``DecideApprovalRequest``：用户批准 / 拒绝。批准**不代表自动执行** ——
   仍由用户在桌面端发起，符合「默认不过度自动化」。

一次性授权（PRD-PUB-004）：``scope='once'`` 的请求一经使用即置
``consumed``；授权与 ``target`` + ``payload`` 内容哈希绑定，换内容即失效。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult

#: 审批状态机
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
STATUS_CONSUMED = "consumed"

_DECIDABLE = (STATUS_PENDING,)

#: 授权范围：一次性 vs 持久（§9.2「一次性或持久授权范围」）
SCOPE_ONCE = "once"
SCOPE_PERSISTENT = "persistent"
_VALID_SCOPES = (SCOPE_ONCE, SCOPE_PERSISTENT)

#: 默认有效期（§9.2「有效期」）：一次性授权 24 小时内有效
_DEFAULT_TTL_HOURS = 24

_DEFAULT_LIMIT = 50


def _now() -> datetime:
    return datetime.now(UTC)


def content_hash(payload: dict[str, Any] | None) -> str:
    """对请求内容取稳定哈希（PRD-PUB-004：授权与内容绑定，换内容即失效）。"""
    canonical = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_request(
    conn: Any,
    *,
    actor: str,
    action_type: str,
    target: str,
    requested_scope: str = SCOPE_ONCE,
    risk_summary: str = "",
    payload: dict[str, Any] | None = None,
    ttl_hours: int = _DEFAULT_TTL_HOURS,
) -> str:
    """写入一条待审批请求，返回其 id。

    供 bus 在拒绝 agent 高风险命令时调用（降级为准备任务），也供
    ``CreateApprovalRequest`` 命令直接调用。
    """
    now = _now()
    request_id = f"apr_{uuid.uuid4().hex}"
    body = dict(payload or {})
    body["content_hash"] = content_hash(payload)
    conn.execute(
        "INSERT INTO approval_requests "
        "(id, actor, action_type, target, requested_scope, risk_summary, "
        "payload, expires_at, status, decision_actor, decision_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            request_id,
            actor,
            action_type,
            target,
            requested_scope,
            risk_summary,
            json.dumps(body, ensure_ascii=False),
            (now + timedelta(hours=ttl_hours)).isoformat(),
            STATUS_PENDING,
            None,
            None,
            now.isoformat(),
        ),
    )
    conn.commit()
    return request_id


def _row_to_dict(row: Any) -> dict[str, Any]:
    """审批行 → §9.2 要求展示的全部要素。"""
    try:
        payload = json.loads(row["payload"]) if row["payload"] else {}
    except (TypeError, ValueError):
        payload = {}
    return {
        "id": str(row["id"]),
        # §9.2：请求者 / 协议或插件
        "actor": str(row["actor"]),
        # §9.2：将执行的动作
        "action_type": str(row["action_type"]),
        # §9.2：目标 Workspace/Project
        "target": str(row["target"]),
        # §9.2：一次性或持久授权范围
        "requested_scope": row["requested_scope"],
        # §9.2：费用或风险
        "risk_summary": row["risk_summary"],
        # §9.2：将读取、修改或上传的数据
        "payload": payload,
        # §9.2：有效期
        "expires_at": row["expires_at"],
        "status": str(row["status"]),
        "decision_actor": row["decision_actor"],
        "decision_at": row["decision_at"],
        "created_at": str(row["created_at"]),
    }


def _expire_stale(conn: Any) -> None:
    """把过期的 pending 请求标记为 expired（§9.2 有效期真正生效）。"""
    conn.execute(
        "UPDATE approval_requests SET status=? "
        "WHERE status=? AND expires_at IS NOT NULL AND expires_at < ?",
        (STATUS_EXPIRED, STATUS_PENDING, _now().isoformat()),
    )
    conn.commit()


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由审批中心的三个命令。"""
    conn = deps.repos.conn
    p: dict[str, Any] = env.payload or {}

    if env.commandType == "CreateApprovalRequest":
        action_type = p.get("actionType") or p.get("action_type")
        target = p.get("target") or env.projectId or env.workspaceId
        if not action_type:
            raise DispatchError("INVALID_ARGUMENT", "actionType required")
        scope = str(p.get("scope") or SCOPE_ONCE)
        if scope not in _VALID_SCOPES:
            raise DispatchError(
                "INVALID_ARGUMENT", f"scope must be one of {_VALID_SCOPES}"
            )
        actor = env.actor or {}
        request_id = create_request(
            conn,
            actor=f"{actor.get('type', 'unknown')}:{actor.get('id', 'unknown')}",
            action_type=str(action_type),
            target=str(target),
            requested_scope=scope,
            risk_summary=str(p.get("riskSummary") or p.get("risk_summary") or ""),
            payload=p.get("payload") if isinstance(p.get("payload"), dict) else {},
        )
        row = conn.execute(
            "SELECT * FROM approval_requests WHERE id=?", (request_id,)
        ).fetchone()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"approval": _row_to_dict(row)},
        )

    if env.commandType == "ListApprovalRequests":
        _expire_stale(conn)
        limit = p.get("limit", _DEFAULT_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise DispatchError(
                "INVALID_ARGUMENT", f"limit must be a positive integer, got {limit!r}"
            )
        sql = "SELECT * FROM approval_requests"
        args: list[Any] = []
        status = p.get("status")
        if status is not None:
            if not isinstance(status, str):
                raise DispatchError("INVALID_ARGUMENT", "status must be a string")
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(sql, tuple(args)).fetchall()
        approvals = [_row_to_dict(r) for r in rows]
        pending = sum(1 for a in approvals if a["status"] == STATUS_PENDING)
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"approvals": approvals, "pending_count": pending},
        )

    if env.commandType == "DecideApprovalRequest":
        # 独立命名，避免与上文 CreateApprovalRequest 分支的 str 复用同名（mypy）
        target_id = p.get("approvalId") or p.get("approval_id")
        decision = str(p.get("decision") or "").lower()
        if not target_id:
            raise DispatchError("INVALID_ARGUMENT", "approvalId required")
        if decision not in ("approve", "reject"):
            raise DispatchError(
                "INVALID_ARGUMENT", "decision must be 'approve' or 'reject'"
            )
        _expire_stale(conn)
        row = conn.execute(
            "SELECT * FROM approval_requests WHERE id=?", (str(target_id),)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"approval {target_id!r} not found")
        if str(row["status"]) not in _DECIDABLE:
            # 已决/已过期不可二次裁决，避免「批准一个早已过期的高风险操作」
            raise DispatchError(
                "INVALID_ARGUMENT",
                f"approval already {row['status']}, not decidable",
            )

        actor = env.actor or {}
        new_status = STATUS_APPROVED if decision == "approve" else STATUS_REJECTED
        conn.execute(
            "UPDATE approval_requests SET status=?, decision_actor=?, decision_at=? "
            "WHERE id=?",
            (
                new_status,
                f"{actor.get('type', 'user')}:{actor.get('id', 'unknown')}",
                _now().isoformat(),
                str(target_id),
            ),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM approval_requests WHERE id=?", (str(target_id),)
        ).fetchone()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "approval": _row_to_dict(updated),
                # 批准≠自动执行：仍需用户在桌面端发起（默认不过度自动化）
                "auto_executed": False,
            },
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by approvals handler",
    )
