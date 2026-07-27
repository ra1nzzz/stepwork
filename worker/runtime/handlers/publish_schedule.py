"""定时发布队列命令。

模式区分见 worker/runtime/publish/schedule.py：platform_native 是真正的
无人值守，local_reminder 只是到点提醒 —— 两者绝不能在措辞上混为一谈。
"""

from __future__ import annotations

from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.handlers.publish_common import (
    _row_to_variant,
)
from worker.runtime.models import CommandEnvelope, CommandResult
from worker.runtime.publish import schedule


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    repos = deps.repos
    p: dict[str, Any] = env.payload or {}

    if env.commandType == "SchedulePublish":
        sched_variant_id = p.get("variantId") or p.get("variant_id")
        if not sched_variant_id:
            raise DispatchError("INVALID_ARGUMENT", "variantId required")
        raw_when = p.get("scheduledAt") or p.get("scheduled_at")
        if not raw_when:
            raise DispatchError("INVALID_ARGUMENT", "scheduledAt required")
        try:
            when = schedule.parse_scheduled_at(str(raw_when))
        except ValueError as e:
            raise DispatchError("INVALID_ARGUMENT", f"scheduledAt 解析失败：{e}") from e

        row = repos.conn.execute(
            "SELECT * FROM platform_variants WHERE id=?", (str(sched_variant_id),)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"variant {sched_variant_id!r} not found")
        variant = _row_to_variant(row)
        try:
            record = schedule.create(
                repos.conn,
                workspace_id=env.workspaceId,
                project_id=str(variant["project_id"]),
                variant_id=str(sched_variant_id),
                platform=str(variant.get("platform") or ""),
                scheduled_at=when,
                content_hash=schedule.current_content_hash(
                    repos.conn, str(sched_variant_id)
                ),
                note=str(p.get("note") or ""),
            )
        except ValueError as e:
            raise DispatchError("INVALID_ARGUMENT", str(e)) from e
        record["mode_description"] = schedule.describe_mode(str(record["mode"]))
        return CommandResult(ok=True, commandId=env.commandId, detail=record)

    if env.commandType == "ListScheduledPublishes":
        items = schedule.list_all(
            repos.conn,
            project_id=(p.get("projectId") or p.get("project_id") or None),
            status=(p.get("status") or None),
        )
        for item in items:
            item["mode_description"] = schedule.describe_mode(str(item["mode"]))
        return CommandResult(
            ok=True, commandId=env.commandId, detail={"scheduled": items}
        )

    if env.commandType == "CancelScheduledPublish":
        sched_id = p.get("scheduleId") or p.get("schedule_id")
        if not sched_id:
            raise DispatchError("INVALID_ARGUMENT", "scheduleId required")
        cancelled = schedule.cancel(repos.conn, str(sched_id))
        if not cancelled:
            # 已触发/已取消的不再改动：如实说明，而不是假装成功
            raise DispatchError(
                "INVALID_STATE", f"排期 {sched_id!r} 不存在或已触发，无法取消"
            )
        return CommandResult(
            ok=True, commandId=env.commandId, detail={"cancelled": str(sched_id)}
        )

    if env.commandType == "FireDueSchedules":
        fired = schedule.fire(repos.conn)
        for item in fired:
            item["mode_description"] = schedule.describe_mode(str(item["mode"]))
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"fired": fired, "count": len(fired)},
        )


    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by this publish submodule",
    )
