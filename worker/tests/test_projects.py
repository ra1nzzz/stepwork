"""Tranche 1（T2）：``CreateProject`` / ``DeleteAsset`` handler 测试。

覆盖：

1. ``test_create_project_fresh_db``：全新库（无 workspaces 行、外键开启）上
   CreateProject 成功——回归守卫「未先 ``workspaces.ensure`` 导致 FK 失败」。
2. ``test_create_project_returns_project``：返回 ``detail.project`` 含
   id/title，status 与 0001_init.sql 默认值 / seed 一致（``active``）。
3. ``test_create_project_missing_title``：缺 title → INVALID_ARGUMENT。
4. ``test_delete_asset_removes_row``：删除已导入 asset 后行消失。
5. ``test_delete_asset_not_found``：不存在的 id → NOT_FOUND。
"""

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
    """全新内存库（外键开启）+ 迁移 + Deps。"""
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    return Deps(repos=Repos(conn), ingest=ingest)


def _env(
    command_type: str,
    payload: dict[str, Any] | None = None,
    workspace_id: str = "ws-fresh",
) -> dict[str, Any]:
    """构造最小合规命令信封 dict（对齐 command-envelope.schema.json）。"""
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": workspace_id,
        "payload": payload or {},
        "requestedAt": datetime.now(UTC).isoformat(),
    }


async def test_create_project_fresh_db() -> None:
    """全新库（无 workspaces 行）上 CreateProject 必须成功（先 ensure 再 INSERT）。"""
    deps = _deps()
    res = await dispatch(_env("CreateProject", {"title": "首个项目"}), deps)
    assert res["ok"] is True, res.get("error")
    # workspace 行已被 ensure 出来
    ws = deps.repos.conn.execute(
        "SELECT id FROM workspaces WHERE id=?", ("ws-fresh",)
    ).fetchone()
    assert ws is not None


async def test_create_project_returns_project() -> None:
    """返回 detail.project：id 非空、title 回显、status 与 seed/查询一致。"""
    deps = _deps()
    res = await dispatch(_env("CreateProject", {"title": "My Project"}), deps)
    assert res["ok"] is True
    project = res["detail"]["project"]
    assert project["id"]
    assert project["title"] == "My Project"
    assert project["status"] == "active"
    # 落库行与返回一致，且能被 ListProjects 查回
    listed = await dispatch(_env("ListProjects"), deps)
    assert listed["ok"] is True
    assert [p["id"] for p in listed["detail"]["projects"]] == [project["id"]]


async def test_create_project_missing_title() -> None:
    """缺 title → INVALID_ARGUMENT。"""
    deps = _deps()
    res = await dispatch(_env("CreateProject", {}), deps)
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]


async def test_delete_asset_removes_row() -> None:
    """删除已导入 asset：ok=True 且行消失（文件本体不删除，Tranche 1 范围外）。"""
    deps = _deps()
    imp = await dispatch(
        _env(
            "ImportSource",
            {"local_uri": "file://a.mp4", "content_hash": "h_del", "kind": "video"},
        ),
        deps,
    )
    assert imp["ok"] is True
    asset_id = imp["artifact_ids"][0]

    res = await dispatch(_env("DeleteAsset", {"assetId": asset_id}), deps)
    assert res["ok"] is True
    assert res["detail"]["deleted"] is True
    row = deps.repos.conn.execute(
        "SELECT id FROM source_assets WHERE id=?", (asset_id,)
    ).fetchone()
    assert row is None


async def test_delete_asset_not_found() -> None:
    """不存在的 assetId → NOT_FOUND。"""
    deps = _deps()
    res = await dispatch(_env("DeleteAsset", {"assetId": "asset_nope"}), deps)
    assert res["ok"] is False
    assert "NOT_FOUND" in res["error"]
