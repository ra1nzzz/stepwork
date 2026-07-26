"""定时发布队列（用户需求：要定时发布，但**不要**完全自动发布）。

这两件事看似矛盾，实际能同时成立 —— 关键是分清两种模式：

- ``platform_native``：平台自己有定时发布（抖音 2h~7d、B站 ≤24h、
  小红书 ≤7d）。我们把目标时间填进**平台自己的定时字段**，用户确认提交
  一次，之后由平台在到点时发布。全程零自动点击，却是真正的无人值守。
  这条路完全不违反 ADR-008。
- ``local_reminder``：平台没有可用的原生定时（如视频号）。本地到点只能
  **备好填充包并提醒用户**，不会替他点发布。

第二种模式必须如实呈现。把它包装成「定时发布」是危险的：用户会以为可以
去睡觉，醒来发现根本没发。所以字段名叫 reminder，``unattended`` 明确为
False，UI 文案也照此写。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from worker.runtime.publish.platforms import (
    SCHEDULE_LOCAL,
    SCHEDULE_NATIVE,
    resolve_rules,
    resolve_schedule_mode,
    validate_schedule,
)

STATUS_PENDING = "pending"
#: armed = 已交给平台（原生）或已提醒用户（本地）
STATUS_ARMED = "armed"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"

#: 到期扫描一次最多处理多少条，避免积压时一次性刷屏
MAX_FIRE_BATCH = 50


def parse_scheduled_at(raw: str) -> datetime:
    """解析用户给的时间；无时区的按 UTC 处理。

    naive datetime 直接参与比较会抛 TypeError，且「本地时间还是 UTC」的
    歧义会让定时差 8 小时 —— 统一在入口消解。
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError("scheduledAt 不能为空")
    normalized = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def create(
    conn: Any,
    *,
    workspace_id: str,
    project_id: str,
    variant_id: str,
    platform: str,
    scheduled_at: datetime,
    content_hash: str,
    note: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """排一条定时发布；返回该条记录（含解析出的模式）。"""
    moment = now or datetime.now(UTC)
    rules = resolve_rules(platform)
    issues = validate_schedule(rules, scheduled_at, moment)
    if any(i["level"] == "error" for i in issues):
        raise ValueError(issues[0]["message"])

    mode = resolve_schedule_mode(rules, scheduled_at, moment)
    row_id = f"sched_{uuid.uuid4().hex}"
    stamp = moment.isoformat()
    conn.execute(
        "INSERT INTO scheduled_publishes "
        "(id, workspace_id, project_id, variant_id, platform, scheduled_at, "
        "mode, status, content_hash, note, fired_at, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,?)",
        (
            row_id,
            workspace_id,
            project_id,
            variant_id,
            platform,
            scheduled_at.isoformat(),
            mode,
            STATUS_PENDING,
            content_hash,
            note,
            stamp,
            stamp,
        ),
    )
    conn.commit()
    return {
        "id": row_id,
        "mode": mode,
        "scheduled_at": scheduled_at.isoformat(),
        "platform": rules.id,
        "platform_label": rules.label,
        # 只有原生模式才是真正的无人值守；本地模式到点仍需用户在场
        "unattended": mode == SCHEDULE_NATIVE,
        "note": rules.schedule_note,
        "issues": issues,
        "status": STATUS_PENDING,
    }


def _row_to_dict(row: Any) -> dict[str, Any]:
    item = {key: row[key] for key in row.keys()}
    item["unattended"] = item.get("mode") == SCHEDULE_NATIVE
    return item


def list_all(
    conn: Any, *, project_id: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM scheduled_publishes"
    clauses: list[str] = []
    args: list[Any] = []
    if project_id:
        clauses.append("project_id=?")
        args.append(project_id)
    if status:
        clauses.append("status=?")
        args.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY scheduled_at ASC"
    return [_row_to_dict(r) for r in conn.execute(sql, args).fetchall()]


def cancel(conn: Any, schedule_id: str, *, now: datetime | None = None) -> bool:
    """撤销一条尚未触发的定时。已触发的不再改动（如实反映历史）。"""
    stamp = (now or datetime.now(UTC)).isoformat()
    cur = conn.execute(
        "UPDATE scheduled_publishes SET status=?, updated_at=? "
        "WHERE id=? AND status=?",
        (STATUS_CANCELLED, stamp, schedule_id, STATUS_PENDING),
    )
    conn.commit()
    return bool(cur.rowcount)


def due(conn: Any, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """到点且仍待处理的条目。"""
    moment = (now or datetime.now(UTC)).isoformat()
    rows = conn.execute(
        "SELECT * FROM scheduled_publishes "
        "WHERE status=? AND scheduled_at<=? "
        "ORDER BY scheduled_at ASC LIMIT ?",
        (STATUS_PENDING, moment, MAX_FIRE_BATCH),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def current_content_hash(conn: Any, variant_id: str) -> str:
    """变体当前内容的哈希，用于检测「排期之后又改了稿」。"""
    from worker.runtime.handlers.approvals import content_hash

    row = conn.execute(
        "SELECT title, body, tags FROM platform_variants WHERE id=?", (variant_id,)
    ).fetchone()
    if row is None:
        return ""
    return content_hash(
        {"title": row["title"], "body": row["body"], "tags": row["tags"]}
    )


def fire(conn: Any, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """把到期条目标记为 armed，返回它们（供上层发通知）。

    同时检查内容哈希：排期之后用户又改了稿的，标记 ``content_changed``。
    这跟 PRD-PUB-004「授权与内容绑定」是同一条原则 —— 排的是那份内容，
    换了内容就该重新确认，不能悄悄按旧排期走。
    """
    moment = now or datetime.now(UTC)
    fired: list[dict[str, Any]] = []
    for item in due(conn, now=moment):
        item["content_changed"] = (
            current_content_hash(conn, str(item["variant_id"])) != item["content_hash"]
        )
        conn.execute(
            "UPDATE scheduled_publishes SET status=?, fired_at=?, updated_at=? "
            "WHERE id=? AND status=?",
            (
                STATUS_ARMED,
                moment.isoformat(),
                moment.isoformat(),
                item["id"],
                STATUS_PENDING,
            ),
        )
        item["status"] = STATUS_ARMED
        fired.append(item)
    if fired:
        conn.commit()
    return fired


def describe_mode(mode: str) -> str:
    """给 UI 的一句话说明，措辞必须区分「平台会发」和「提醒你发」。"""
    if mode == SCHEDULE_NATIVE:
        return "到点由平台自动发布（已填入平台自带的定时字段，你只需提交一次）"
    if mode == SCHEDULE_LOCAL:
        return "该平台无可用的原生定时，到点会备好内容并提醒你手动发布"
    return mode
