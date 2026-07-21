"""任务引擎高层 API（W3-W4 Batch 0）。

对 ``Repos`` 的薄封装，handler 与 Command Bus 只调这里，不直接碰 SQL。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from worker.runtime.db.repos import Repos, _row_to_job
from worker.runtime.jobs.lease import is_expired
from worker.runtime.models import Job, JobStage, JobState


def create_job(
    repos: Repos,
    job_type: str,
    payload: dict[str, Any],
    stage: JobStage | None = None,
    max_attempts: int = 3,
) -> Job:
    """创建并落库一个任务（默认 PENDING）。"""
    j = Job(job_type=job_type, payload=payload, stage=stage, max_attempts=max_attempts)
    repos.jobs.create(j)
    return j


def record_heartbeat(repos: Repos, job_id: str) -> None:
    """更新任务心跳时间戳（看门狗依据）。"""
    repos.jobs.update_heartbeat(job_id, datetime.now(UTC).isoformat())


def transition(
    repos: Repos,
    job_id: str,
    to_state: JobState,
    progress: float | None = None,
    error: str | None = None,
    stage: JobStage | None = None,
) -> Job:
    """状态迁移（job 看门狗/worker 调用）。"""
    return repos.jobs.update_state(job_id, to_state, progress, error, stage)


def retry_eligible(repos: Repos, now: datetime | None = None) -> list[Job]:
    """返回 FAILED/EXPIRED 且未超 ``max_attempts`` 的可重试任务。"""
    now = now or datetime.now(UTC)
    rows = repos.conn.execute(
        "SELECT * FROM jobs WHERE state IN (?,?) AND attempt_count < max_attempts",
        (JobState.FAILED.value, JobState.EXPIRED.value),
    ).fetchall()
    return [_row_to_job(r) for r in rows]


def is_job_expired(job: Job, now: datetime | None = None) -> bool:
    """透传租约过期判定（供看门狗使用）。"""
    return is_expired(job, now)
