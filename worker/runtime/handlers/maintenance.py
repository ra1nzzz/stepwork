"""维护类命令：手动清理 与 审计事件查询。

补齐两处「后端有能力、用户够不到」的缺口：

- ``RunCleanup``（PRD-SRC-005）：清理策略此前只在 worker 启动时按
  ``cleanupMode`` 自动执行；选了 ``manual`` 的用户没有任何触发入口，
  等于三个选项里有一个是死的。本命令提供显式触发。
- ``ListAuditEvents``（PRD-ANA-006）：``audit_events`` 此前全仓只有
  INSERT，没有任何读取路径——「执行后可审计」只体现为当次会话的
  detail 回显，重启即不可查。本命令把已落库的调用记录读出来。
"""

from __future__ import annotations

import json
from typing import Any

from worker.runtime.cleanup import (
    resolve_cleanup_config,
    resolve_stepwork_home,
    retention_sweep,
)
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult

# 审计查询默认/最大返回条数（防止一次拉爆 UI）
_DEFAULT_AUDIT_LIMIT = 50
_MAX_AUDIT_LIMIT = 500

# 手动清理允许的模式：immediate=全清，scheduled=按保留期清。
# manual 在此处无意义（本命令本身就是「手动触发」），故不接受。
_RUNNABLE_MODES: tuple[str, ...] = ("immediate", "scheduled")


def _workspace_settings(conn: Any, workspace_id: str) -> dict[str, Any]:
    """读取工作区 settings（缺失/畸形回退空 dict）。"""
    row = conn.execute(
        "SELECT settings FROM workspaces WHERE id=?", (workspace_id,)
    ).fetchone()
    if row is None or not row["settings"]:
        return {}
    try:
        parsed = json.loads(row["settings"])
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由 ``RunCleanup`` / ``ListAuditEvents``。"""
    conn = deps.repos.conn
    p: dict[str, Any] = env.payload or {}

    if env.commandType == "RunCleanup":
        settings = _workspace_settings(conn, env.workspaceId)
        retention_days, configured_mode = resolve_cleanup_config(settings)
        # 显式 mode 覆盖工作区配置；配置为 manual 时手动触发按 immediate 执行
        # （用户点「立即清理」的意图就是现在清，而不是什么都不做）
        requested = p.get("mode")
        if requested is not None:
            mode = str(requested)
            if mode not in _RUNNABLE_MODES:
                raise DispatchError(
                    "INVALID_ARGUMENT",
                    f"mode must be one of {_RUNNABLE_MODES}, got {mode!r}",
                )
        else:
            mode = "immediate" if configured_mode == "manual" else configured_mode

        home = resolve_stepwork_home()
        removed = retention_sweep(home, retention_days, mode)
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "removed": removed,
                "mode": mode,
                "retention_days": retention_days,
                "configured_mode": configured_mode,
            },
        )

    if env.commandType == "ListAuditEvents":
        limit = p.get("limit", _DEFAULT_AUDIT_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise DispatchError(
                "INVALID_ARGUMENT", f"limit must be a positive integer, got {limit!r}"
            )
        limit = min(limit, _MAX_AUDIT_LIMIT)

        # 表定义见 migrations/0002（基础列）+ 0005（event_type / payload）；
        # 时间列名是 timestamp，不是 created_at。
        sql = (
            "SELECT id, actor, source_protocol, command, target, result, "
            "correlation_id, timestamp, event_type, payload FROM audit_events"
        )
        args: list[Any] = []
        event_type = p.get("eventType") or p.get("event_type")
        if event_type is not None:
            if not isinstance(event_type, str):
                raise DispatchError("INVALID_ARGUMENT", "eventType must be a string")
            sql += " WHERE event_type=?"
            args.append(event_type)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        args.append(limit)

        rows = conn.execute(sql, tuple(args)).fetchall()
        events: list[dict[str, Any]] = []
        for r in rows:
            try:
                parsed = json.loads(r["payload"]) if r["payload"] else {}
            except (TypeError, ValueError):
                parsed = {}
            events.append(
                {
                    "id": r["id"],
                    "actor": r["actor"],
                    "source_protocol": r["source_protocol"],
                    "command": r["command"],
                    "target": r["target"],
                    "result": r["result"],
                    "correlation_id": r["correlation_id"],
                    "timestamp": r["timestamp"],
                    "event_type": r["event_type"],
                    "payload": parsed,
                }
            )
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"events": events, "count": len(events)},
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by maintenance handler",
    )
