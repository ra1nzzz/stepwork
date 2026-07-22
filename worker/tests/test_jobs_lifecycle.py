"""jobs.lifecycle 共享生命周期单测（R3 非阻项：补专属单测）。

覆盖 :func:`content_job` 的三条路径：
- 成功进入 → job 置 ``RUNNING``、``_JobCtx`` 暴露 job/project_id/repos；
- 工作抛非 ``DispatchError`` → 转译 ``transition(FAILED)`` + ``DispatchError(fail_code)``；
- 工作抛 ``DispatchError`` → 原样透传（不重复包裹）。
"""

from __future__ import annotations

from pathlib import Path

from worker.runtime.commands.bus import DispatchError
from worker.runtime.commands.envelope import parse_envelope
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.jobs import content_job
from worker.runtime.models import CommandEnvelope, JobStage, JobState

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _repos() -> Repos:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    return Repos(c)


def _env() -> CommandEnvelope:
    raw = {
        "commandId": "cmd-1",
        "commandType": "GenerateTopic",
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": "ws-1",
        "payload": {"source_version_id": "cv-1"},
        "requestedAt": "2026-07-22T00:00:00Z",
    }
    return parse_envelope(raw)


async def test_content_job_success_sets_running() -> None:
    repos = _repos()
    env = _env()
    async with content_job(
        repos, job_type="topic", stage=JobStage.PROPOSING, env=env, fail_code="TOPIC_FAILED"
    ) as ctx:
        got = repos.jobs.get(ctx.job.id)
        assert got is not None
        assert got.state == JobState.RUNNING
        assert ctx.project_id
        assert ctx.repos is repos
    # 正常退出：CM 不代行 SUCCEEDED，job 仍 RUNNING
    final = repos.jobs.get(ctx.job.id)
    assert final is not None
    assert final.state == JobState.RUNNING


async def test_content_job_translates_unexpected_error() -> None:
    repos = _repos()
    env = _env()
    captured: dict[str, str] = {}
    raised: DispatchError | None = None
    try:
        async with content_job(
            repos, job_type="topic", stage=JobStage.PROPOSING, env=env, fail_code="TOPIC_FAILED"
        ) as ctx:
            captured["id"] = ctx.job.id
            raise ValueError("boom")
    except DispatchError as e:
        raised = e
    assert raised is not None, "expected DispatchError"
    assert "boom" in raised.message
    assert raised.code == "TOPIC_FAILED"
    final = repos.jobs.get(captured["id"])
    assert final is not None
    assert final.state == JobState.FAILED


async def test_content_job_passthrough_dispatch_error() -> None:
    repos = _repos()
    env = _env()
    raised: DispatchError | None = None
    try:
        async with content_job(
            repos, job_type="topic", stage=JobStage.PROPOSING, env=env, fail_code="TOPIC_FAILED"
        ):
            raise DispatchError("CUSTOM_CODE", "keep-me")
    except DispatchError as e:
        raised = e
    assert raised is not None
    assert raised.code == "CUSTOM_CODE"
    assert "keep-me" in raised.message
