"""``SaveAnalysis`` 命令处理（Tranche 2，PRD-ANA-006 编辑保存 = 版本链追加）。

仿 ``SaveScript``：每次保存新建一条 ``analysis`` ContentVersion，
``parent_version_id`` 指向上一版；分析报告编辑后不覆盖历史。
payload：``{projectId?, content(JSON string of report), parentVersionId?}``。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
    ContentVersion,
)


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``SaveAnalysis``。"""
    repos = deps.repos
    p: dict[str, Any] = env.payload or {}
    content = p.get("content")
    if not content:
        raise DispatchError("INVALID_ARGUMENT", "content required")

    content_str = content if isinstance(content, str) else json.dumps(
        content, ensure_ascii=False
    )
    # 分析稿契约为 JSON string of report——畸形 JSON 直接拒绝，
    # 避免落库后前端回读崩溃。
    try:
        json.loads(content_str)
    except (TypeError, ValueError) as e:
        raise DispatchError(
            "INVALID_ARGUMENT", f"content must be a JSON string: {e}"
        ) from None

    repos.workspaces.ensure(env.workspaceId)
    project_id = (
        p.get("projectId")
        or env.projectId
        or repos.projects.get_or_create_default(env.workspaceId).id
    )

    # 验证 parent 属同项目（版本链完整性；兼容 camelCase / snake_case）
    parent_id = p.get("parentVersionId") or p.get("parent_version_id")
    if parent_id:
        pv = repos.content_versions.get(parent_id)
        if pv is None or pv.project_id != project_id:
            raise DispatchError("NOT_FOUND", f"parent {parent_id} not found")

    cv = ContentVersion(
        project_id=project_id,
        parent_version_id=parent_id,
        content_type="analysis",
        content=content_str,
        content_hash=hashlib.sha256(content_str.encode("utf-8")).hexdigest(),
        producer={"kind": "user-analysis"},
    )
    cv_id = repos.content_versions.insert(cv)
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        artifact_ids=[cv_id],
        detail={"parent": parent_id, "version_id": cv_id},
    )
