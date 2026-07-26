"""Tranche 1（T5）：``ListJobs`` 查询命令测试。

覆盖：

1. ``test_list_jobs_desc_order``：按 ``created_at DESC`` 返回、字段与
   GetJobStatus 的 job dict 同构。
2. ``test_list_jobs_states_filter``：``states`` 过滤（小写 JobState value）。
3. ``test_list_jobs_limit``：``limit`` 截断（取最新 N 条）。
4. ``test_list_jobs_empty_table``：空表返回 ``{jobs: []}``。
5. ``test_list_jobs_bad_limit``：非法 limit → INVALID_ARGUMENT。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import Job, JobState

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _deps() -> Deps:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    return Deps(repos=Repos(conn))


def _env(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造最小合规 ``ListJobs`` 信封 dict。"""
    return {
        "commandId": "cid-ListJobs",
        "commandType": "ListJobs",
        "schemaVersion": "1",
        "actor": {"type": "desktop", "id": "desktop-test"},
        "source": "ui",
        "workspaceId": "ws-1",
        "payload": payload or {},
        "requestedAt": datetime.now(UTC).isoformat(),
    }


def _seed_jobs(deps: Deps) -> list[str]:
    """播种 3 个 job（created_at 递增，状态各异），返回 id 列表（创建序）。"""
    specs = [
        ("transcribe", JobState.SUCCEEDED, "2026-07-20T00:00:01+00:00"),
        ("render_source", JobState.RUNNING, "2026-07-20T00:00:02+00:00"),
        ("topic", JobState.FAILED, "2026-07-20T00:00:03+00:00"),
    ]
    ids: list[str] = []
    for job_type, state, ts in specs:
        j = Job(job_type=job_type, state=state, created_at=ts, updated_at=ts)
        deps.repos.jobs.create(j)
        ids.append(j.id)
    return ids


async def test_list_jobs_desc_order() -> None:
    """全量返回按 created_at DESC；字段与 GetJobStatus job dict 同构。"""
    deps = _deps()
    ids = _seed_jobs(deps)
    res = await dispatch(_env(), deps)
    assert res["ok"] is True
    jobs = res["detail"]["jobs"]
    assert [j["id"] for j in jobs] == list(reversed(ids))
    expected_keys = {
        "id", "job_type", "state", "stage", "progress",
        "attempt_count", "error_code", "created_at", "updated_at",
    }
    assert set(jobs[0].keys()) == expected_keys
    assert jobs[0]["state"] == JobState.FAILED.value


async def test_list_jobs_states_filter() -> None:
    """``states`` 过滤：只返回匹配状态的 job。"""
    deps = _deps()
    ids = _seed_jobs(deps)
    res = await dispatch(
        _env({"states": ["running", "failed"]}), deps
    )
    assert res["ok"] is True
    jobs = res["detail"]["jobs"]
    assert [j["id"] for j in jobs] == [ids[2], ids[1]]
    assert {j["state"] for j in jobs} == {"running", "failed"}


async def test_list_jobs_limit() -> None:
    """``limit`` 截断：取最新 N 条。"""
    deps = _deps()
    ids = _seed_jobs(deps)
    res = await dispatch(_env({"limit": 2}), deps)
    assert res["ok"] is True
    jobs = res["detail"]["jobs"]
    assert [j["id"] for j in jobs] == [ids[2], ids[1]]


async def test_list_jobs_empty_table() -> None:
    """空表返回 ``{jobs: []}``。"""
    deps = _deps()
    res = await dispatch(_env(), deps)
    assert res["ok"] is True
    assert res["detail"]["jobs"] == []


async def test_list_jobs_bad_limit() -> None:
    """非法 limit（0 / 负数 / 非整数）→ INVALID_ARGUMENT。"""
    deps = _deps()
    for bad in (0, -1, "ten"):
        res = await dispatch(_env({"limit": bad}), deps)
        assert res["ok"] is False
        assert "INVALID_ARGUMENT" in res["error"]
