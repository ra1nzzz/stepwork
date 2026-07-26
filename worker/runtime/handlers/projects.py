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

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime.audit import EVENT_PROJECT_CREATED, record_event
from worker.runtime.cleanup import assets_root, resolve_stepwork_home
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult

#: 单个项目最多标签数（防止 UI/查询被超长标签串拖垮）
_MAX_TAGS = 20
_MAX_TAG_LEN = 40


def _validate_tags(raw: Any) -> list[str]:
    """校验并归一化标签列表（PRD-WS-003）。"""
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
        raise DispatchError("INVALID_ARGUMENT", "tags must be a list of strings")
    tags: list[str] = []
    for item in raw:
        tag = item.strip()
        if not tag:
            continue
        if len(tag) > _MAX_TAG_LEN:
            raise DispatchError(
                "INVALID_ARGUMENT", f"tag too long (max {_MAX_TAG_LEN}): {tag[:20]}…"
            )
        if tag not in tags:
            tags.append(tag)
    if len(tags) > _MAX_TAGS:
        raise DispatchError("INVALID_ARGUMENT", f"too many tags (max {_MAX_TAGS})")
    return tags


def _ensure_project_dir(project_id: str, title: str, created_at: str) -> str | None:
    """创建项目默认目录并写入 project.json（PRD-WS-002）。

    失败（权限/磁盘）只记为 None，绝不阻塞项目创建 —— 目录是便利设施，
    项目数据的事实源仍是 SQLite。
    """
    try:
        root = resolve_stepwork_home() / "projects" / project_id
        root.mkdir(parents=True, exist_ok=True)
        (root / "project.json").write_text(
            json.dumps(
                {"id": project_id, "title": title, "created_at": created_at},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return str(root)
    except OSError:
        return None


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
        tags = _validate_tags(p.get("tags"))
        # T2 修复：新库上 workspaces 行可能不存在，直插会触发 FK 失败
        deps.repos.workspaces.ensure(env.workspaceId)
        pid = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        deps.repos.conn.execute(
            "INSERT INTO content_projects "
            "(id, workspace_id, title, status, brand_profile_id, "
            "current_content_version_id, created_at, updated_at, tags, "
            "last_accessed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, env.workspaceId, title, "active",
             brand_profile_id, None, now, now,
             json.dumps(tags, ensure_ascii=False), now),
        )
        deps.repos.conn.commit()
        # PRD-WS-002「新建项目自动创建默认目录」：项目目录此前从不创建，
        # 本地文件导入也不落项目目录。这里建出来并写入 project.json 元数据，
        # 使项目在磁盘上可被用户直接找到。失败不阻塞建项目（可能无权限）。
        project_dir = _ensure_project_dir(pid, title, now)
        # PRD §14 埋点：项目创建（此前无任何事件）
        record_event(
            deps.repos.conn, env, EVENT_PROJECT_CREATED,
            {"project_id": pid, "title": title, "tags": tags},
        )
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
                "tags": tags,
                "last_accessed_at": now,
                "project_dir": project_dir,
            }},
        )

    if env.commandType == "SetProjectTags":
        # PRD-WS-003：标签可编辑（建项目时可带，之后也能改）
        p = env.payload or {}
        # 独立命名，避免与 CreateProject 分支生成的 str 复用同名（mypy）
        target_pid = p.get("projectId") or p.get("project_id") or env.projectId
        if not target_pid:
            raise DispatchError("INVALID_ARGUMENT", "missing projectId")
        tags = _validate_tags(p.get("tags"))
        cur = deps.repos.conn.execute(
            "UPDATE content_projects SET tags=?, updated_at=? WHERE id=?",
            (
                json.dumps(tags, ensure_ascii=False),
                datetime.now(UTC).isoformat(),
                str(target_pid),
            ),
        )
        deps.repos.conn.commit()
        if cur.rowcount == 0:
            raise DispatchError("NOT_FOUND", f"project {target_pid!r} not found")
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"project_id": str(target_pid), "tags": tags},
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
