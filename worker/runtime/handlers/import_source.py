"""``ImportSource`` 命令处理（W3，Batch 0 参考实现，验证 Command Bus 端到端）。

职责：
1. 解析 envelope.payload（local_uri / kind / content_hash / metadata）
2. 确保目标 workspace 存在（按 id 幂等插入）
3. 解析目标 project（``env.projectId`` 或缺省项目）
4. 以 content_hash 去重写入 ``source_assets``
5. 返回 :class:`CommandResult`
"""

from __future__ import annotations

from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult, SourceAsset


def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``ImportSource``。"""
    repos = deps.repos
    p: dict[str, Any] = env.payload

    # 确保目标工作区存在（上游未必先建；按 id 幂等插入）
    repos.workspaces.ensure(env.workspaceId)

    project_id = env.projectId
    if project_id is None:
        project_id = repos.projects.get_or_create_default(env.workspaceId).id

    local_uri = p.get("local_uri")
    if not local_uri:
        raise DispatchError("INVALID_ARGUMENT", "local_uri is required")

    kind = str(p.get("kind", "video"))
    content_hash = p.get("content_hash")
    if not content_hash and deps.ingest is not None:
        content_hash = deps.ingest.hash_file(local_uri)
    if not content_hash:
        raise DispatchError(
            "INVALID_ARGUMENT",
            "content_hash unavailable and ingest.hash_file not configured",
        )

    asset = SourceAsset(
        project_id=project_id,
        kind=kind,
        local_uri=local_uri,
        original_uri=p.get("original_uri"),
        content_hash=content_hash,
        rights_declaration=p.get("rights_declaration"),
        metadata=p.get("metadata", {}),
    )
    asset_id = repos.source_assets.insert_dedup(asset)

    return CommandResult(
        ok=True,
        commandId=env.commandId,
        artifact_ids=[asset_id],
        detail={"asset_id": asset_id, "dedup": asset_id != asset.id},
    )
