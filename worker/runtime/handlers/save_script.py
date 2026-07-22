"""``SaveScript`` 命令处理（W5，自动保存 = 版本链追加）。

每次保存新建一条 ``script`` ContentVersion，``parent_version_id`` 指向上
一版；刷新/重启后读 ``parent`` 链即可恢复最新稿（Gate：不丢稿）。
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
    """处理 ``SaveScript``。"""
    repos = deps.repos
    p: dict[str, Any] = env.payload
    content = p.get("content")
    if not content:
        raise DispatchError("INVALID_ARGUMENT", "content required")

    project_id = env.projectId or repos.projects.get_or_create_default(
        env.workspaceId
    ).id

    # 验证 parent 属同项目（版本链完整性）
    parent_id = p.get("parent_version_id")
    if parent_id:
        pv = repos.content_versions.get(parent_id)
        if pv is None or pv.project_id != project_id:
            raise DispatchError(
                "NOT_FOUND", f"parent {parent_id} not found"
            )

    content_str = content if isinstance(content, str) else json.dumps(
        content, ensure_ascii=False
    )
    cv = ContentVersion(
        project_id=project_id,
        parent_version_id=parent_id,
        content_type="script",
        content=content_str,
        content_hash=hashlib.sha256(content_str.encode("utf-8")).hexdigest(),
        producer={"kind": "user-script", "editor": "tiptap"},
    )
    cv_id = repos.content_versions.insert(cv)
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        artifact_ids=[cv_id],
        detail={"parent": parent_id, "version_id": cv_id},
    )
