"""Workspace 命令处理（Tranche 2，PRD-WS-001）。

四个命令：

- ``CreateWorkspace``：payload {name} → 新建工作区行（root_path 落在
  ``$STEPWORK_HOME/workspaces/<id>``）→ detail.workspace。name 唯一，
  冲突转译为 ``CONFLICT``。
- ``RenameWorkspace``：payload {workspaceId, name} → 改名（同样唯一约束）。
- ``ArchiveWorkspace``：payload {workspaceId} → 写 ``archived_at``
  （0001 已有列，幂等：已归档时保持原时间戳）。
- ``ListWorkspaces``：payload {includeArchived?} → detail.workspaces
  （缺省不含已归档）。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult, Workspace


def _resolve_stepwork_home() -> Path:
    """解析 ``$STEPWORK_HOME``，缺省回退到 ``~/STEPWORK``（与 bootstrap.py 一致）。"""
    home = os.environ.get("STEPWORK_HOME") or str(Path.home() / "STEPWORK")
    return Path(home)


def _row_to_dict(row: Any) -> dict[str, Any]:
    """把 ``workspaces`` 行转为可序列化 dict（settings 不外泄，仅结构字段）。"""
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "root_path": str(row["root_path"]),
        "created_at": str(row["created_at"]),
        "archived_at": (
            str(row["archived_at"]) if row["archived_at"] is not None else None
        ),
    }


def _get_row(conn: Any, ws_id: str) -> Any:
    return conn.execute("SELECT * FROM workspaces WHERE id=?", (ws_id,)).fetchone()


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由 Workspace 四命令。"""
    conn = deps.repos.conn
    p: dict[str, Any] = env.payload or {}

    if env.commandType == "CreateWorkspace":
        name = p.get("name")
        if not name or not isinstance(name, str):
            raise DispatchError("INVALID_ARGUMENT", "name required")
        ws = Workspace(name=name, root_path="")
        ws.root_path = str(_resolve_stepwork_home() / "workspaces" / ws.id)
        try:
            deps.repos.workspaces.insert(ws)
        except sqlite3.IntegrityError:
            conn.rollback()
            raise DispatchError(
                "CONFLICT", f"workspace name {name!r} already exists"
            ) from None
        row = _get_row(conn, ws.id)
        return CommandResult(
            ok=True, commandId=env.commandId,
            detail={"workspace": _row_to_dict(row)},
        )

    if env.commandType == "RenameWorkspace":
        ws_id = p.get("workspaceId")
        name = p.get("name")
        if not ws_id or not name or not isinstance(name, str):
            raise DispatchError("INVALID_ARGUMENT", "workspaceId and name required")
        if _get_row(conn, ws_id) is None:
            raise DispatchError("NOT_FOUND", f"workspace {ws_id!r} not found")
        try:
            conn.execute(
                "UPDATE workspaces SET name=? WHERE id=?", (name, ws_id)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            raise DispatchError(
                "CONFLICT", f"workspace name {name!r} already exists"
            ) from None
        return CommandResult(
            ok=True, commandId=env.commandId,
            detail={"workspace": _row_to_dict(_get_row(conn, ws_id))},
        )

    if env.commandType == "ArchiveWorkspace":
        ws_id = p.get("workspaceId")
        if not ws_id:
            raise DispatchError("INVALID_ARGUMENT", "workspaceId required")
        row = _get_row(conn, ws_id)
        if row is None:
            raise DispatchError("NOT_FOUND", f"workspace {ws_id!r} not found")
        # 幂等：已归档保持原时间戳
        if row["archived_at"] is None:
            conn.execute(
                "UPDATE workspaces SET archived_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), ws_id),
            )
            conn.commit()
        return CommandResult(
            ok=True, commandId=env.commandId,
            detail={"workspace": _row_to_dict(_get_row(conn, ws_id))},
        )

    if env.commandType == "ListWorkspaces":
        include_archived = bool(p.get("includeArchived", False))
        sql = "SELECT * FROM workspaces"
        if not include_archived:
            sql += " WHERE archived_at IS NULL"
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql).fetchall()
        return CommandResult(
            ok=True, commandId=env.commandId,
            detail={"workspaces": [_row_to_dict(r) for r in rows]},
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by workspaces handler",
    )
