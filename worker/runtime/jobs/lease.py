"""租约（Lease）机制（W3-W4 Batch 0）。

Worker 崩溃（``kill -9``）后，其持有 lease 的 job 会因 ``lease_expires_at``
过期被扫描为 ``EXPIRED``，进而由 ``engine.retry_eligible`` 重新入队——
这是 STRATEGY_PLAN W2 Gate「kill -9 后无数据丢失」的基石。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Optional

from worker.runtime.db.repos import _row_to_job
from worker.runtime.models import Job, JobState


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    return datetime.fromisoformat(ts)


def acquire(conn: sqlite3.Connection, job_id: str, owner: str, ttl_sec: int = 300) -> bool:
    """为 ``job_id`` 获取租约（仅 PENDING/EXPIRED 可抢占）。

    Args:
        conn: SQLite 连接。
        job_id: 任务 id。
        owner: 持锁标识（如 worker session token）。
        ttl_sec: 租约存活秒数。

    Returns:
        是否成功获取（``rowcount>0``）。
    """
    now = datetime.now(UTC)
    expires = (now + timedelta(seconds=ttl_sec)).isoformat()
    cur = conn.execute(
        "UPDATE jobs SET state=?, lease_owner=?, lease_expires_at=?, updated_at=? "
        "WHERE id=? AND (state=? OR state=?)",
        (JobState.LEASED.value, owner, expires, now.isoformat(), job_id,
         JobState.PENDING.value, JobState.EXPIRED.value),
    )
    conn.commit()
    return cur.rowcount > 0


def is_expired(job: Job, now: Optional[datetime] = None) -> bool:
    """判定一个 LEASED job 的租约是否已过期。"""
    if job.state != JobState.LEASED:
        return False
    if not job.lease_expires_at:
        return False
    now = now or datetime.now(UTC)
    parsed = _parse(job.lease_expires_at)
    return parsed is not None and parsed < now


def sweep_expired(conn: sqlite3.Connection, now: Optional[datetime] = None) -> list[Job]:
    """扫描并把过期 lease 的 job 置为 ``EXPIRED``。

    Returns:
        被置为过期的 job 列表。
    """
    now = now or datetime.now(UTC)
    rows = conn.execute(
        "SELECT * FROM jobs WHERE state=? AND lease_expires_at IS NOT NULL "
        "AND lease_expires_at < ?",
        (JobState.LEASED.value, now.isoformat()),
    ).fetchall()
    jobs = [_row_to_job(r) for r in rows]
    for j in jobs:
        conn.execute(
            "UPDATE jobs SET state=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? "
            "WHERE id=?",
            (JobState.EXPIRED.value, now.isoformat(), j.id),
        )
    conn.commit()
    return jobs
