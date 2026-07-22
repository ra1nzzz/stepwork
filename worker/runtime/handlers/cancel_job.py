"""``CancelJob`` 命令处理（W6，SYSTEM_SPEC §10.4）。

UI 取消渲染任务时调用：置位对应 ``threading.Event``（由
:mod:`worker.runtime.render.ffmpeg_runner` 终止 FFmpeg 子进程），
并将任务标记为 ``CANCELLED_REQUESTED``；最终状态由
``render_source`` 捕获 ``FFmpegCancelled`` 后落 ``CANCELLED``。
"""

from __future__ import annotations

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import transition
from worker.runtime.jobs.cancel import request as request_cancel
from worker.runtime.models import CommandEnvelope, CommandResult, JobState


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``CancelJob``。"""
    job_id = (env.payload or {}).get("job_id")
    if not job_id:
        raise DispatchError("INVALID_ARGUMENT", "job_id required")
    fired = request_cancel(job_id)
    transition(deps.repos, job_id, JobState.CANCELLED_REQUESTED)
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        job_id=job_id,
        detail={"cancelled": fired},
    )
