"""``CancelJob`` 命令处理（W6，SYSTEM_SPEC §10.4）。

UI 取消渲染任务时调用：置位对应 ``threading.Event``（由
:mod:`worker.runtime.render.ffmpeg_runner` 终止 FFmpeg 子进程），
并将任务标记为 ``CANCELLED_REQUESTED``；最终状态由
``render_source`` 捕获 ``FFmpegCancelled`` 后落 ``CANCELLED``。

状态守卫：仅 ``PENDING`` / ``LEASED`` / ``RUNNING`` /
``CANCELLED_REQUESTED``（幂等重取消）可被取消。终态任务
（SUCCEEDED/FAILED/CANCELLED/EXPIRED）绝不回写——否则会把
``SUCCEEDED`` 覆盖成 ``CANCELLED_REQUESTED``，任务永远悬在中间态。
本 handler 在单事件循环线程内执行且 get→transition 之间无 await 点，
check-then-update 对本进程串行化的 SQLite 访问是竞态安全的。
"""

from __future__ import annotations

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import transition
from worker.runtime.jobs.cancel import request as request_cancel
from worker.runtime.models import CommandEnvelope, CommandResult, JobState

# 终态集合（与 JobRepo._TERMINAL_STATES 一致）：进入后不可再被取消
_TERMINAL_STATES: frozenset[JobState] = frozenset(
    {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.EXPIRED}
)


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``CancelJob``（仅取消未终态任务；终态/缺失干净返回，不崩溃）。"""
    job_id = (env.payload or {}).get("job_id")
    if not job_id:
        raise DispatchError("INVALID_ARGUMENT", "job_id required")
    job = deps.repos.jobs.get(job_id)
    if job is None:
        raise DispatchError("NOT_FOUND", f"job {job_id!r} not found")
    if job.state in _TERMINAL_STATES:
        # 终态不可取消：保持原状态，仅告知调用方「已终态」
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            job_id=job_id,
            detail={
                "cancelled": False,
                "already_terminal": True,
                "state": job.state.value,
            },
        )
    fired = request_cancel(job_id)
    transition(deps.repos, job_id, JobState.CANCELLED_REQUESTED)
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        job_id=job_id,
        detail={"cancelled": fired},
    )
