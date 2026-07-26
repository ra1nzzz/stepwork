"""共享 handler 生命周期（T1 去重）。

5 个 content 类 handler（``generate_topic`` / ``generate_script`` /
``analyze_source`` / ``transcribe_source`` / ``render_source``）都重复了同一套
「workspace 确保 + project 解析 → create_job + lease + transition(RUNNING) →
工作 → 失败转译 FAILED / 成功 persist + SUCCEEDED」骨架。本模块抽取为：

- :func:`content_job`：异步上下文管理器，进入时确保 workspace+project、创建并
  租约 job、置 ``RUNNING``，``yield`` 一个 :class:`_JobCtx`；退出时若抛出非
  ``DispatchError`` 的异常，则 ``transition(FAILED)`` 并转译为 ``DispatchError``
  （用 ``fail_code`` 保留各 handler 的领域错误码）。
- :func:`persist_content_version`：构造 ``ContentVersion``、落库、``SUCCEEDED``、
  记录 ``result``，返回 ``cv_id``。
- :func:`emit_job_progress` / :func:`notify_job_progress`：``job.progress``
  进度通知（Tranche 1 契约）。``notify`` 缺省（测试内嵌调用）时为 no-op，
  发送失败一律吞掉，绝不让 handler 失败。

租约顺序（T5 修复）：``create_job``（PENDING）→ ``acquire``（PENDING→LEASED，
写入 lease_owner / lease_expires_at）→ ``transition(RUNNING)``。旧实现先置
RUNNING 再 acquire，而 ``acquire`` 只匹配 PENDING/EXPIRED，租约是静默 no-op，
``kill -9`` 恢复链路（sweep_expired → retry_eligible）形同虚设。

所有 DB 写入仍在主事件循环线程执行（``db_conn`` 为 ``check_same_thread=True``），
因此本模块内的同步 DB 调用与 handler 主流程一致，不触碰 worker 线程。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.jobs.cancel import clear as clear_cancel
from worker.runtime.jobs.cancel import register_task
from worker.runtime.jobs.engine import create_job, record_result, transition
from worker.runtime.jobs.lease import acquire
from worker.runtime.models import CommandEnvelope, ContentVersion, Job, JobStage, JobState

logger = logging.getLogger("worker.runtime.jobs")


def _job_progress_params(job: Job) -> dict[str, Any]:
    """构造 ``job.progress`` notification 的 params（Tranche 1 契约形状）。"""
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "state": job.state.value,
        "stage": job.stage.value if job.stage else None,
        "progress": job.progress,
        "error_code": job.error_code,
    }


async def notify_job_progress(notify: Any, job: Job) -> None:
    """异步路径发送 ``job.progress`` 通知（fire-safe）。

    ``notify`` 为 ``None``（未注入，测试内嵌调用）时 no-op；发送抛出的任何
    异常都被吞掉并降级为日志，绝不让业务 handler 失败。
    """
    if notify is None:
        return
    try:
        await notify("job.progress", _job_progress_params(job))
    except Exception:  # noqa: BLE001 - 通知失败绝不影响业务流
        logger.debug("job.progress notify failed job=%s", job.id, exc_info=True)


def emit_job_progress(notify: Any, job: Job) -> None:
    """同步路径 fire-and-forget 发送 ``job.progress`` 通知（fire-safe）。

    供无法 ``await`` 的调用点使用（如 :func:`persist_content_version` 与
    渲染进度回调）：在当前事件循环上调度一个通知 task。无运行中事件循环 /
    ``notify`` 抛错等任何异常都被吞掉。
    """
    if notify is None:
        return
    coro = notify_job_progress(notify, job)
    try:
        task = asyncio.get_running_loop().create_task(coro)
        # 排干 task 异常，避免 "exception was never retrieved" 噪声
        task.add_done_callback(
            lambda t: t.exception() if not t.cancelled() else None
        )
    except Exception:  # noqa: BLE001 - 通知失败绝不影响业务流
        with contextlib.suppress(Exception):
            coro.close()


@dataclass
class _JobCtx:
    """``content_job`` 上下文：暴露 job / project_id / repos 与进度上报。"""

    job: Job
    project_id: str
    repos: Any
    #: 进度通知回调（由 content_job 注入），供 progress() 使用
    notify: Any = None

    def progress(self, value: float, stage: JobStage | None = None) -> None:
        """上报中间进度（UX §10.2「长任务必须有阶段、进度」）。

        此前只有渲染发真实进度，分析/角度/脚本只在进入 RUNNING（0）与
        落库（1.0）各发一次，UI 进度条整段停在 0%。本方法让这些任务也能
        在关键节点推进阶段与进度。

        非阻塞：失败只记日志，绝不影响业务（进度是观测，不是事实源）。
        """
        try:
            updated = transition(
                self.repos,
                self.job.id,
                JobState.RUNNING,
                progress=max(0.0, min(1.0, value)),
                stage=stage,
            )
            self.job = updated
            emit_job_progress(self.notify, updated)
        except Exception:  # noqa: BLE001 - 进度上报失败不影响任务本身
            logger.exception("progress report failed job=%s", self.job.id)


@asynccontextmanager
async def content_job(
    repos: Any,
    *,
    job_type: str,
    stage: JobStage,
    env: CommandEnvelope,
    fail_code: str = "HANDLER_FAILED",
    lease: str | None = None,
    payload: dict[str, Any] | None = None,
    notify: Any = None,
) -> AsyncIterator[_JobCtx]:
    """确保 workspace+project，创建 job、获取租约、置 ``RUNNING``，yield 上下文。

    进入阶段（workspace 确保 / project 解析）不创建 job，因此输入校验失败
    （``NOT_FOUND`` / ``UNAVAILABLE`` / ``INVALID_ARGUMENT`` 等）不会残留
    半截 job。job 创建后若工作抛非 ``DispatchError`` 异常，则转译为
    ``transition(FAILED)`` + ``DispatchError(fail_code, …)``，与改造前各
    handler 的 ``except Exception`` 行为一致。

    Args:
        repos: 注入的 ``Repos``。
        job_type: ``jobs.job_type``（如 ``"topic"`` / ``"render_source"``）。
        stage: 业务阶段（``JobStage``）。
        env: 已校验的 :class:`CommandEnvelope`。
        fail_code: 工作异常时转译的 ``DispatchError`` 错误码（保留领域语义）。
        lease: 非空时为 job 获取租约（``kill -9`` 恢复用），值为持锁标识。
            顺序：create（PENDING）→ acquire（LEASED）→ RUNNING，租约字段
            在整个 RUNNING 期间保持（终态迁移时由 ``update_state`` 清除）。
        payload: 落库到 ``jobs.payload`` 的内容；缺省用 ``env.payload``
            （analyze / transcribe 会用精简后的自定义 payload）。
        notify: 可选异步进度通知回调（``deps.notify``）；进入 RUNNING 与
            失败转译 FAILED 时发送 ``job.progress``。
    """
    repos.workspaces.ensure(env.workspaceId)
    project_id = env.projectId or repos.projects.get_or_create_default(
        env.workspaceId
    ).id
    job = create_job(
        repos, job_type, payload if payload is not None else env.payload, stage=stage
    )
    # T5：先租约（acquire 只匹配 PENDING/EXPIRED），再进入 RUNNING
    if lease:
        acquire(repos.conn, job.id, owner=lease, ttl_sec=600)
    job = transition(repos, job.id, JobState.RUNNING)
    await notify_job_progress(notify, job)

    ctx = _JobCtx(job=job, project_id=project_id, repos=repos, notify=notify)
    # UX §10.2：登记当前 Task，使 CancelJob 能真正打断异步任务
    # （渲染另用 threading.Event，见 jobs/cancel.py）。
    current = asyncio.current_task()
    if current is not None:
        register_task(job.id, current)
    try:
        yield ctx
    except asyncio.CancelledError:
        # 用户取消：落 CANCELLED 终态并通知，然后把取消继续向上传播
        # （绝不吞掉 CancelledError，否则事件循环的取消语义会被破坏）
        cancelled = transition(repos, job.id, JobState.CANCELLED)
        with contextlib.suppress(Exception):
            await notify_job_progress(notify, cancelled)
        raise
    except DispatchError:
        # handler 自行转译的领域错误（含输入校验 / FFmpeg 特定分支），不再重复处理
        raise
    except Exception as e:  # noqa: BLE001 - 统一兜底为领域错误
        failed = transition(repos, job.id, JobState.FAILED, error=str(e)[:200])
        await notify_job_progress(notify, failed)
        raise DispatchError(fail_code, str(e)[:200]) from None
    finally:
        clear_cancel(job.id)


def persist_content_version(
    repos: Any,
    job: Job,
    *,
    project_id: str,
    content: str,
    content_type: str,
    content_hash: str,
    producer: dict[str, Any],
    stage: JobStage,
    parent_version_id: str | None = None,
    notify: Any = None,
) -> str:
    """落库内容版本并标记 job ``SUCCEEDED`` + 记录 ``result``。

    等价各 handler 中重复的「``ContentVersion`` 构造 → insert →
    ``transition(SUCCEEDED)`` → ``record_result``」四步。``notify`` 注入时
    fire-and-forget 发送 ``SUCCEEDED`` 的 ``job.progress`` 通知。

    Returns:
        新建 ``content_versions`` 行的 id。
    """
    cv = ContentVersion(
        project_id=project_id,
        parent_version_id=parent_version_id,
        content_type=content_type,
        content=content,
        content_hash=content_hash,
        producer=producer,
    )
    cv_id: str = repos.content_versions.insert(cv)
    succeeded = transition(
        repos, job.id, JobState.SUCCEEDED, progress=1.0, error=None, stage=stage
    )
    record_result(repos, job.id, [cv_id])
    emit_job_progress(notify, succeeded)
    return cv_id
