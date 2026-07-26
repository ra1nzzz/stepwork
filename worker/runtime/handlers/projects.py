"""``CreateProject`` / ``DeleteAsset`` 命令处理（项目流 + 素材重置）。

- ``CreateProject``：先 ``workspaces.ensure``（新库直插会触发 FK 失败），
  再在 ``content_projects`` 新建一行（status=active，与 0001_init.sql 默认值 /
  ``ContentProject`` 模型 / seed_demo.py 一致）。
- ``DeleteAsset``：按 id 删除 ``source_assets`` 行（先 SELECT 校验存在）。
  ``content_versions`` 无 ``source_asset_id`` 外键（见 0001_init.sql），
  故不做关联清理。文件本体不删除（Tranche 1 范围外）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由 ``CreateProject`` / ``DeleteAsset`` 两个写命令。"""
    if env.commandType == "CreateProject":
        p = env.payload or {}
        title = p.get("title")
        if not title:
            raise DispatchError("INVALID_ARGUMENT", "missing title")
        brand_profile_id = p.get("brandProfileId")
        # T2 修复：新库上 workspaces 行可能不存在，直插会触发 FK 失败
        deps.repos.workspaces.ensure(env.workspaceId)
        pid = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        deps.repos.conn.execute(
            "INSERT INTO content_projects "
            "(id, workspace_id, title, status, brand_profile_id, "
            "current_content_version_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, env.workspaceId, title, "active",
             brand_profile_id, None, now, now),
        )
        deps.repos.conn.commit()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"project": {
                "id": pid,
                "workspace_id": env.workspaceId,
                "title": title,
                "status": "active",
                "brand_profile_id": brand_profile_id,
                "current_content_version_id": None,
                "created_at": now,
                "updated_at": now,
            }},
        )

    if env.commandType == "DeleteAsset":
        p = env.payload or {}
        asset_id = p.get("assetId")
        if not asset_id:
            raise DispatchError("INVALID_ARGUMENT", "missing assetId")
        row = deps.repos.conn.execute(
            "SELECT id FROM source_assets WHERE id=?", (asset_id,)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"asset {asset_id!r} not found")
        deps.repos.conn.execute(
            "DELETE FROM source_assets WHERE id=?", (asset_id,)
        )
        deps.repos.conn.commit()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"deleted": True, "asset_id": asset_id},
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by projects handler",
    )
