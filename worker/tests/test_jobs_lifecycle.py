"""jobs.lifecycle 共享生命周期单测（R3 非阻项：补专属单测）。

覆盖 :func:`content_job` 的三条路径：
- 成功进入 → job 置 ``RUNNING``、``_JobCtx`` 暴露 job/project_id/repos；
- 工作抛非 ``DispatchError`` → 转译 ``transition(FAILED)`` + ``DispatchError(fail_code)``；
- 工作抛 ``DispatchError`` → 原样透传（不重复包裹）。

Tranche 1 追加：
- ``job.progress`` 进度通知（RUNNING → SUCCEEDED 顺序、FAILED 路径、
  notify 抛错不影响业务流）；
- 租约顺序修复（create → acquire → RUNNING，RUNNING 期间 lease_owner 保持）。
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.commands.envelope import parse_envelope
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.jobs import content_job, persist_content_version
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


# ===== Tranche 1：job.progress 进度通知 =====


def _make_notify(events: list[tuple[str, dict[str, Any]]]) -> Any:
    """构造记录型 fake notify（契约签名 ``async def notify(method, params)``）。"""

    async def notify(method: str, params: dict[str, Any]) -> None:
        events.append((method, params))

    return notify


async def test_content_job_notify_running_then_succeeded() -> None:
    """一次完整 content_job 流：fake notify 依序收到 RUNNING → SUCCEEDED。"""
    repos = _repos()
    env = _env()
    events: list[tuple[str, dict[str, Any]]] = []
    notify = _make_notify(events)

    async with content_job(
        repos,
        job_type="topic",
        stage=JobStage.PROPOSING,
        env=env,
        fail_code="TOPIC_FAILED",
        notify=notify,
    ) as ctx:
        content = '{"angles": []}'
        persist_content_version(
            repos,
            ctx.job,
            project_id=ctx.project_id,
            content=content,
            content_type="topic_proposal",
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            producer={"kind": "test"},
            stage=JobStage.PROPOSING,
            notify=notify,
        )
    # SUCCEEDED 通知是 fire-and-forget task，让出一轮事件循环使其落地
    await asyncio.sleep(0)

    assert [m for m, _ in events] == ["job.progress", "job.progress"]
    running, succeeded = (p for _, p in events)
    assert running["job_id"] == ctx.job.id
    assert running["state"] == JobState.RUNNING.value
    assert running["job_type"] == "topic"
    assert succeeded["job_id"] == ctx.job.id
    assert succeeded["state"] == JobState.SUCCEEDED.value
    assert succeeded["progress"] == 1.0
    assert succeeded["error_code"] is None


async def test_content_job_notify_failed_on_exception() -> None:
    """工作抛异常：fake notify 依序收到 RUNNING → FAILED（含 error_code）。"""
    repos = _repos()
    env = _env()
    events: list[tuple[str, dict[str, Any]]] = []
    notify = _make_notify(events)

    try:
        async with content_job(
            repos,
            job_type="topic",
            stage=JobStage.PROPOSING,
            env=env,
            fail_code="TOPIC_FAILED",
            notify=notify,
        ):
            raise ValueError("boom")
    except DispatchError:
        pass

    states = [p["state"] for _, p in events]
    assert states == [JobState.RUNNING.value, JobState.FAILED.value]
    assert events[-1][1]["error_code"] == "boom"


async def test_content_job_notify_failure_is_swallowed() -> None:
    """notify 自身抛错绝不影响业务流（fire-safe 契约）。"""
    repos = _repos()
    env = _env()

    async def bad_notify(method: str, params: dict[str, Any]) -> None:
        raise RuntimeError("notify channel down")

    async with content_job(
        repos,
        job_type="topic",
        stage=JobStage.PROPOSING,
        env=env,
        fail_code="TOPIC_FAILED",
        notify=bad_notify,
    ) as ctx:
        pass
    got = repos.jobs.get(ctx.job.id)
    assert got is not None
    assert got.state == JobState.RUNNING


# ===== Tranche 1（T5）：租约顺序修复 =====


async def test_content_job_lease_takes_effect_while_running() -> None:
    """lease 参数：RUNNING 期间 ``lease_owner`` / ``lease_expires_at`` 已写入。

    旧实现先 ``transition(RUNNING)`` 再 ``acquire``，而 ``acquire`` 只匹配
    PENDING/EXPIRED，租约是静默 no-op；修复后 create → acquire → RUNNING。
    """
    repos = _repos()
    env = _env()
    async with content_job(
        repos,
        job_type="topic",
        stage=JobStage.PROPOSING,
        env=env,
        fail_code="TOPIC_FAILED",
        lease="test-owner",
    ) as ctx:
        got = repos.jobs.get(ctx.job.id)
        assert got is not None
        assert got.state == JobState.RUNNING
        assert got.lease_owner == "test-owner"
        assert got.lease_expires_at is not None


# ----- UX §10.2：分析/转写/脚本必须有真实中间进度 -----


async def test_ctx_progress_emits_intermediate_updates() -> None:
    """content_job 上下文的 progress() 落库并发通知。"""
    repos = _repos()
    conn = repos.conn
    repos.workspaces.ensure("ws-1")

    sent: list[dict[str, Any]] = []

    async def _notify(method: str, params: dict[str, Any]) -> None:
        sent.append({"method": method, **params})

    async with content_job(
        repos,
        job_type="analyze",
        stage=JobStage.ANALYZING,
        env=_env(),
        notify=_notify,
    ) as ctx:
        ctx.progress(0.2, JobStage.ANALYZING)
        ctx.progress(0.8, JobStage.ANALYZING)
        job_id = ctx.job.id

    row = conn.execute("SELECT progress FROM jobs WHERE id=?", (job_id,)).fetchone()
    # 最后一次上报的进度已落库
    assert row["progress"] == 0.8

    await asyncio.sleep(0)  # 让 fire-and-forget 的通知 task 跑完
    progresses = [s["progress"] for s in sent if s["method"] == "job.progress"]
    # 除了进入 RUNNING 的 0，还应有中间进度（此前整段停在 0）
    assert any(p in (0.2, 0.8) for p in progresses), progresses


async def test_ctx_progress_failure_does_not_break_job(
    monkeypatch: Any,
) -> None:
    """进度上报失败只记日志，不影响任务本身（进度是观测不是事实源）。

    用 monkeypatch 让 transition 抛错，而**不是**改 ctx.job.id ——
    后者会让 content_job 的 finally 用错误的 key 清理取消注册表，
    把脏条目泄漏给后续测试（这个坑真踩过一次）。
    """
    import worker.runtime.jobs.lifecycle as lifecycle_mod

    repos = _repos()
    repos.workspaces.ensure("ws-1")
    async with content_job(
        repos, job_type="analyze", stage=JobStage.ANALYZING, env=_env()
    ) as ctx:
        def _boom(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("db down")

        monkeypatch.setattr(lifecycle_mod, "transition", _boom)
        ctx.progress(0.5)  # 不应抛
        monkeypatch.undo()
