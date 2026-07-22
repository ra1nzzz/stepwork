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

所有 DB 写入仍在主事件循环线程执行（``db_conn`` 为 ``check_same_thread=True``），
因此本模块内的同步 DB 调用与 handler 主流程一致，不触碰 worker 线程。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.jobs.engine import create_job, record_result, transition
from worker.runtime.jobs.lease import acquire
from worker.runtime.models import CommandEnvelope, ContentVersion, Job, JobStage, JobState


@dataclass
class _JobCtx:
    """``content_job`` 上下文：暴露 job / project_id / repos。"""

    job: Job
    project_id: str
    repos: Any


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
) -> AsyncIterator[_JobCtx]:
    """确保 workspace+project，创建并租约 job，置 ``RUNNING``，yield 上下文。

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
        payload: 落库到 ``jobs.payload`` 的内容；缺省用 ``env.payload``
            （analyze / transcribe 会用精简后的自定义 payload）。
    """
    repos.workspaces.ensure(env.workspaceId)
    project_id = env.projectId or repos.projects.get_or_create_default(
        env.workspaceId
    ).id
    job = create_job(
        repos, job_type, payload if payload is not None else env.payload, stage=stage
    )
    job = transition(repos, job.id, JobState.RUNNING)
    if lease:
        acquire(repos.conn, job.id, owner=lease, ttl_sec=600)

    ctx = _JobCtx(job=job, project_id=project_id, repos=repos)
    try:
        yield ctx
    except DispatchError:
        # handler 自行转译的领域错误（含输入校验 / FFmpeg 特定分支），不再重复处理
        raise
    except Exception as e:  # noqa: BLE001 - 统一兜底为领域错误
        transition(repos, job.id, JobState.FAILED, error=str(e)[:200])
        raise DispatchError(fail_code, str(e)[:200]) from None


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
) -> str:
    """落库内容版本并标记 job ``SUCCEEDED`` + 记录 ``result``。

    等价各 handler 中重复的「``ContentVersion`` 构造 → insert →
    ``transition(SUCCEEDED)`` → ``record_result``」四步。

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
    transition(
        repos, job.id, JobState.SUCCEEDED, progress=1.0, error=None, stage=stage
    )
    record_result(repos, job.id, [cv_id])
    return cv_id
