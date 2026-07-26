"""``CreateProject`` / ``DeleteAsset`` 命令处理（项目流 + 素材重置）。

- ``CreateProject``：先 ``workspaces.ensure``（新库直插会触发 FK 失败），
  再在 ``content_projects`` 新建一行（status=active，与 0001_init.sql 默认值 /
  ``ContentProject`` 模型 / seed_demo.py 一致）。
- ``DeleteAsset``（Tranche 2，PRD-SRC-005）：按 id 删除 ``source_assets``
  行（先 SELECT 校验存在）。文件本体：路径位于
  ``$STEPWORK_HOME/assets/`` 内（严格前缀校验，防目录逃逸/符号链接）
  时一并删除；在外则只删行并在 detail 注明 ``file_kept=true``。
  ``content_versions`` 无 ``source_asset_id`` 外键（见 0001_init.sql），
  故不做关联清理。
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from worker.runtime.cleanup import assets_root
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult


def _is_inside_assets_dir(path: Path) -> bool:
    """严格前缀校验：``path`` 解析后必须落在资产目录内（防目录逃逸）。"""
    try:
        resolved = path.resolve()
        root = assets_root().resolve()
    except OSError:
        return False
    return resolved.is_relative_to(root)


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
            "SELECT id, local_uri FROM source_assets WHERE id=?", (asset_id,)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"asset {asset_id!r} not found")
        deps.repos.conn.execute(
            "DELETE FROM source_assets WHERE id=?", (asset_id,)
        )
        deps.repos.conn.commit()

        # 文件本体：仅在 $STEPWORK_HOME/assets/ 内才删除（严格前缀校验）
        local_uri = str(row["local_uri"] or "")
        file_path = Path(local_uri[7:] if local_uri.startswith("file://") else local_uri)
        detail: dict[str, object] = {"deleted": True, "asset_id": asset_id}
        if local_uri and _is_inside_assets_dir(file_path):
            try:
                os.remove(file_path)
                detail["file_deleted"] = True
            except FileNotFoundError:
                detail["file_deleted"] = True  # 已不存在：视为删除完成
            except OSError:
                detail["file_kept"] = True  # 占用/权限受限：行已删，文件保留
        else:
            detail["file_kept"] = True
        return CommandResult(ok=True, commandId=env.commandId, detail=detail)

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by projects handler",
    )
