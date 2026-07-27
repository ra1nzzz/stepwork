"""Tranche 2：Workspace 命令（Create/Rename/Archive/List）测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _deps() -> Deps:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    return Deps(repos=Repos(conn), ingest=ingest)


def _env(
    command_type: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": "ws-env",
        "payload": payload or {},
        "requestedAt": datetime.now(UTC).isoformat(),
    }


async def test_create_workspace() -> None:
    deps = _deps()
    res = await dispatch(_env("CreateWorkspace", {"name": "我的工作区"}), deps)
    assert res["ok"] is True, res.get("error")
    ws = res["detail"]["workspace"]
    assert ws["id"].startswith("ws_")
    assert ws["name"] == "我的工作区"
    assert ws["archived_at"] is None
    assert ws["root_path"].endswith(ws["id"])


async def test_create_workspace_duplicate_name_conflict() -> None:
    deps = _deps()
    await dispatch(_env("CreateWorkspace", {"name": "dup"}), deps)
    res = await dispatch(_env("CreateWorkspace", {"name": "dup"}), deps)
    assert res["ok"] is False
    assert "CONFLICT" in res["error"]


async def test_rename_workspace() -> None:
    deps = _deps()
    created = await dispatch(_env("CreateWorkspace", {"name": "旧名"}), deps)
    ws_id = created["detail"]["workspace"]["id"]
    res = await dispatch(
        _env("RenameWorkspace", {"workspaceId": ws_id, "name": "新名"}), deps
    )
    assert res["ok"] is True
    assert res["detail"]["workspace"]["name"] == "新名"


async def test_rename_workspace_not_found() -> None:
    deps = _deps()
    res = await dispatch(
        _env("RenameWorkspace", {"workspaceId": "ws_nope", "name": "x"}), deps
    )
    assert res["ok"] is False
    assert "NOT_FOUND" in res["error"]


async def test_archive_and_list_workspaces() -> None:
    deps = _deps()
    a = await dispatch(_env("CreateWorkspace", {"name": "A"}), deps)
    b = await dispatch(_env("CreateWorkspace", {"name": "B"}), deps)
    ws_a = a["detail"]["workspace"]["id"]
    ws_b = b["detail"]["workspace"]["id"]

    archived = await dispatch(
        _env("ArchiveWorkspace", {"workspaceId": ws_a}), deps
    )
    assert archived["ok"] is True
    assert archived["detail"]["workspace"]["archived_at"] is not None

    # 缺省列表不含已归档
    listed = await dispatch(_env("ListWorkspaces"), deps)
    ids = [w["id"] for w in listed["detail"]["workspaces"]]
    assert ws_b in ids
    assert ws_a not in ids

    # includeArchived=True 时包含
    listed_all = await dispatch(
        _env("ListWorkspaces", {"includeArchived": True}), deps
    )
    ids_all = [w["id"] for w in listed_all["detail"]["workspaces"]]
    assert ws_a in ids_all and ws_b in ids_all


async def test_archive_workspace_idempotent() -> None:
    deps = _deps()
    created = await dispatch(_env("CreateWorkspace", {"name": "C"}), deps)
    ws_id = created["detail"]["workspace"]["id"]
    first = await dispatch(_env("ArchiveWorkspace", {"workspaceId": ws_id}), deps)
    ts1 = first["detail"]["workspace"]["archived_at"]
    second = await dispatch(_env("ArchiveWorkspace", {"workspaceId": ws_id}), deps)
    assert second["ok"] is True
    assert second["detail"]["workspace"]["archived_at"] == ts1  # 幂等保持时间戳
