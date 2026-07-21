"""Batch 0：Job 引擎（lease / sweep / transition / retry）测试。"""

from __future__ import annotations

from pathlib import Path

from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.jobs import (
    acquire,
    create_job,
    is_expired,
    retry_eligible,
    sweep_expired,
    transition,
)
from worker.runtime.models import JobState

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _repos() -> Repos:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    return Repos(c)


def test_create_and_lease() -> None:
    repos = _repos()
    j = create_job(repos, "transcribe", {"asset_id": "a"})
    assert j.state == JobState.PENDING

    ok = acquire(repos.conn, j.id, "owner-1", ttl_sec=300)
    assert ok

    got = repos.jobs.get(j.id)
    assert got is not None
    assert got.state == JobState.LEASED
    assert got.lease_owner == "owner-1"
    assert not is_expired(got)


def test_sweep_expired() -> None:
    repos = _repos()
    j = create_job(repos, "transcribe", {})
    # ttl 为负 → 立即过期
    acquire(repos.conn, j.id, "o", ttl_sec=-10)

    swept = sweep_expired(repos.conn)
    ids = {s.id for s in swept}
    assert j.id in ids

    got = repos.jobs.get(j.id)
    assert got is not None
    assert got.state == JobState.EXPIRED


def test_transition_and_retry() -> None:
    repos = _repos()
    j = create_job(repos, "transcribe", {}, max_attempts=3)
    transition(repos, j.id, JobState.RUNNING, progress=0.5)
    got = repos.jobs.get(j.id)
    assert got is not None
    assert got.progress == 0.5

    transition(repos, j.id, JobState.FAILED, error="boom")
    got = repos.jobs.get(j.id)
    assert got is not None
    assert got.error_code == "boom"

    eligible = retry_eligible(repos)
    assert any(e.id == j.id for e in eligible)
