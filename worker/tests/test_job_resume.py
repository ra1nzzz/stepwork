"""Tranche 1（T5）：任务恢复（启动孤儿清理 + 终态租约清除）测试。

覆盖：

1. ``test_bootstrap_recovers_orphaned_running_job``：库里残留 RUNNING job →
   ``bootstrap_db`` 后置 ``EXPIRED``（清租约字段，``error_code='worker restarted'``），
   且 ``retry_eligible`` 可见。
2. ``test_bootstrap_recovers_orphaned_leased_job``：残留 LEASED 同样被恢复。
3. ``test_bootstrap_recover_jobs_flag_off``：``recover_jobs=False``（进程内门面
   ``app.run_command`` 路径）不触碰在途 job。
4. ``test_terminal_transition_clears_lease``：迁移到终态（SUCCEEDED/FAILED）
   时 ``lease_owner`` / ``lease_expires_at`` 被清除；非终态迁移保留租约。
"""

from __future__ import annotations

from pathlib import Path

from worker.runtime.bootstrap import bootstrap_db
from worker.runtime.db.connection import connect, in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.jobs import acquire, create_job, retry_eligible, transition
from worker.runtime.models import JobState
from worker.runtime.state import WorkerState

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _seed_job_in_state(db_path: str, state: JobState, lease_owner: str | None) -> str:
    """在 ``db_path`` 建库 + 播种一个处于指定状态的 job，返回 job id。"""
    conn = connect(db_path)
    run_migrations(conn, _MIG_DIR)
    repos = Repos(conn)
    j = create_job(repos, "render_source", {"k": "v"})
    if lease_owner is not None:
        acquire(conn, j.id, owner=lease_owner, ttl_sec=600)
    if state != JobState.LEASED:
        transition(repos, j.id, state)
    conn.close()
    return j.id


def test_bootstrap_recovers_orphaned_running_job(tmp_path: Path) -> None:
    """残留 RUNNING job 在 bootstrap 后应置 EXPIRED（本 worker 是唯一执行者）。"""
    db_path = str(tmp_path / "stepwork.db")
    job_id = _seed_job_in_state(db_path, JobState.RUNNING, lease_owner="dead-worker")

    state = WorkerState()
    bootstrap_db(state, db_path=db_path)
    try:
        repos = Repos(state.db_conn)
        got = repos.jobs.get(job_id)
        assert got is not None
        assert got.state == JobState.EXPIRED
        assert got.lease_owner is None
        assert got.lease_expires_at is None
        assert got.error_code == "worker restarted"
        # retry_eligible 必须能看到（attempt_count=0 < max_attempts=3）
        assert any(j.id == job_id for j in retry_eligible(repos))
    finally:
        state.db_conn.close()


def test_bootstrap_recovers_orphaned_leased_job(tmp_path: Path) -> None:
    """残留 LEASED job（租约未过期）同样在 bootstrap 后被恢复为 EXPIRED。"""
    db_path = str(tmp_path / "stepwork.db")
    job_id = _seed_job_in_state(db_path, JobState.LEASED, lease_owner="dead-worker")

    state = WorkerState()
    bootstrap_db(state, db_path=db_path)
    try:
        got = Repos(state.db_conn).jobs.get(job_id)
        assert got is not None
        assert got.state == JobState.EXPIRED
        assert got.lease_owner is None
    finally:
        state.db_conn.close()


def test_bootstrap_recover_jobs_flag_off(tmp_path: Path) -> None:
    """``recover_jobs=False``：进程内门面路径不得误杀在途 RUNNING job。"""
    db_path = str(tmp_path / "stepwork.db")
    job_id = _seed_job_in_state(db_path, JobState.RUNNING, lease_owner=None)

    state = WorkerState()
    bootstrap_db(state, db_path=db_path, recover_jobs=False)
    try:
        got = Repos(state.db_conn).jobs.get(job_id)
        assert got is not None
        assert got.state == JobState.RUNNING
    finally:
        state.db_conn.close()


def test_terminal_transition_clears_lease() -> None:
    """终态迁移清除租约字段；非终态迁移（RUNNING）保留。"""
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    repos = Repos(conn)

    j = create_job(repos, "render_source", {})
    assert acquire(conn, j.id, owner="o1", ttl_sec=600)
    running = transition(repos, j.id, JobState.RUNNING)
    assert running.lease_owner == "o1"
    assert running.lease_expires_at is not None

    done = transition(repos, j.id, JobState.SUCCEEDED, progress=1.0)
    assert done.lease_owner is None
    assert done.lease_expires_at is None

    j2 = create_job(repos, "render_source", {})
    assert acquire(conn, j2.id, owner="o2", ttl_sec=600)
    transition(repos, j2.id, JobState.RUNNING)
    failed = transition(repos, j2.id, JobState.FAILED, error="boom")
    assert failed.lease_owner is None
    assert failed.lease_expires_at is None
