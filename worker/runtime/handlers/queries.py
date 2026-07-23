"""只读查询类命令处理（W7 Phase 3）。

三个查询 handler，全部只读、不写库：

- ``ListProjects``：列出某工作区下的内容项目。
- ``GetProject``：按 id 取单个项目（兼容 ``payload.projectId`` /
  ``payload.project_id`` / 信封顶层 ``projectId`` 三种来源）。
- ``GetJobStatus``：按 id 取任务状态。

``Repos`` 暂未暴露 list / get-by-id 等读方法，故在 ``deps.repos.conn`` 上
做只读 ``SELECT``；读取严格只读，绝不修改任何状态。
"""

from __future__ import annotations

from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult


def _project_row_to_dict(row: Any) -> dict[str, Any]:
    """把 ``content_projects`` 行转为可序列化的 dict（列名 → 值）。"""
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "title": str(row["title"]),
        "status": str(row["status"]),
        "brand_profile_id": (
            str(row["brand_profile_id"])
            if row["brand_profile_id"] is not None
            else None
        ),
        "current_content_version_id": (
            str(row["current_content_version_id"])
            if row["current_content_version_id"] is not None
            else None
        ),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _resolve_project_id(env: CommandEnvelope) -> str | None:
    """从 payload 或信封顶层解析 projectId（兼容两种命名）。"""
    payload = env.payload or {}
    return payload.get("projectId") or payload.get("project_id") or env.projectId


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由 ``ListProjects`` / ``GetProject`` / ``GetJobStatus`` 三个查询命令。"""
    if env.commandType == "ListProjects":
        rows = deps.repos.conn.execute(
            "SELECT * FROM content_projects WHERE workspace_id=? "
            "ORDER BY created_at DESC",
            (env.workspaceId,),
        ).fetchall()
        projects = [_project_row_to_dict(r) for r in rows]
        return CommandResult(
            ok=True, commandId=env.commandId, detail={"projects": projects}
        )

    if env.commandType == "GetProject":
        pid = _resolve_project_id(env)
        if not pid:
            raise DispatchError("INVALID_ARGUMENT", "missing projectId")
        row = deps.repos.conn.execute(
            "SELECT * FROM content_projects WHERE id=?", (pid,)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"project {pid!r} not found")
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"project": _project_row_to_dict(row)},
        )

    if env.commandType == "GetJobStatus":
        payload = env.payload or {}
        job_id = payload.get("jobId") or payload.get("job_id")
        if not job_id:
            raise DispatchError("INVALID_ARGUMENT", "missing jobId")
        job = deps.repos.jobs.get(job_id)
        if job is None:
            raise DispatchError("NOT_FOUND", f"job {job_id!r} not found")
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "job": {
                    "id": job.id,
                    "job_type": job.job_type,
                    "state": job.state.value,
                    "stage": job.stage.value if job.stage else None,
                    "progress": job.progress,
                    "attempt_count": job.attempt_count,
                    "error_code": job.error_code,
                    "created_at": job.created_at,
                    "updated_at": job.updated_at,
                }
            },
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by queries handler",
    )
