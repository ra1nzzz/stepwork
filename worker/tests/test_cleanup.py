"""Tranche 2：临时清理（PRD-SRC-005）测试。

覆盖：

1. DeleteAsset：资产目录内文件一并删除；目录外 / 逃逸路径只删行
   （detail.file_kept=true）。
2. retention_sweep：scheduled 按 retentionDays（mtime）/ immediate 全清 /
   manual 不清。
3. run_retention_sweep：读工作区 settings（cleanupMode=manual 跳过）。
4. resolve_cleanup_config 默认值与畸形兜底。
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from worker.runtime import ingest
from worker.runtime.cleanup import (
    DEFAULT_CLEANUP_MODE,
    DEFAULT_RETENTION_DAYS,
    resolve_cleanup_config,
    retention_sweep,
    run_retention_sweep,
)
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
        "workspaceId": "ws-clean",
        "payload": payload or {},
        "requestedAt": datetime.now(UTC).isoformat(),
    }


async def _import_file(deps: Deps, path: Path) -> str:
    res = await dispatch(
        _env(
            "ImportSource",
            {"local_uri": str(path), "content_hash": f"h_{path.name}"},
        ),
        deps,
    )
    assert res["ok"] is True, res.get("error")
    return str(res["artifact_ids"][0])


async def test_delete_asset_removes_file_inside_assets_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    asset_file = tmp_path / "assets" / "prj-1" / "clip.mp4"
    asset_file.parent.mkdir(parents=True)
    asset_file.write_bytes(b"data")

    deps = _deps()
    asset_id = await _import_file(deps, asset_file)
    res = await dispatch(_env("DeleteAsset", {"assetId": asset_id}), deps)
    assert res["ok"] is True
    assert res["detail"].get("file_deleted") is True
    assert not asset_file.exists()


async def test_delete_asset_keeps_file_outside_assets_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    outside = tmp_path / "user-media" / "keep.mp4"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"data")

    deps = _deps()
    asset_id = await _import_file(deps, outside)
    res = await dispatch(_env("DeleteAsset", {"assetId": asset_id}), deps)
    assert res["ok"] is True
    assert res["detail"].get("file_kept") is True
    assert outside.exists()  # 目录外文件绝不删除
    # 行仍被删除
    row = deps.repos.conn.execute(
        "SELECT id FROM source_assets WHERE id=?", (asset_id,)
    ).fetchone()
    assert row is None


async def test_delete_asset_escape_path_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``assets/../`` 目录逃逸路径：解析后在外 → 只删行不删文件。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    (tmp_path / "assets").mkdir()
    victim = tmp_path / "victim.db"
    victim.write_bytes(b"data")
    escape_uri = str(tmp_path / "assets" / ".." / "victim.db")

    deps = _deps()
    res_imp = await dispatch(
        _env(
            "ImportSource",
            {"local_uri": escape_uri, "content_hash": "h_escape"},
        ),
        deps,
    )
    asset_id = res_imp["artifact_ids"][0]
    res = await dispatch(_env("DeleteAsset", {"assetId": asset_id}), deps)
    assert res["ok"] is True
    assert res["detail"].get("file_kept") is True
    assert victim.exists()  # 逃逸防护：绝不删除资产目录外的文件


def _make_file(path: Path, age_days: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    if age_days > 0:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))
    return path


def test_retention_sweep_scheduled_by_age(tmp_path: Path) -> None:
    old_file = _make_file(tmp_path / "tmp" / "old.part", age_days=10)
    new_file = _make_file(tmp_path / "tmp" / "new.bin", age_days=1)
    stale_part = _make_file(
        tmp_path / "assets" / "prj" / "dl.mp4.part", age_days=10
    )
    asset = _make_file(tmp_path / "assets" / "prj" / "keep.mp4", age_days=10)

    deleted = retention_sweep(tmp_path, retention_days=7, mode="scheduled")
    assert deleted == 2
    assert not old_file.exists()
    assert not stale_part.exists()
    assert new_file.exists()  # 未过期
    assert asset.exists()  # 正式资产不在清扫范围


def test_retention_sweep_immediate_all(tmp_path: Path) -> None:
    f1 = _make_file(tmp_path / "tmp" / "a.bin")
    f2 = _make_file(tmp_path / "assets" / "p" / "b.mp4.part")
    keep = _make_file(tmp_path / "assets" / "p" / "b.mp4")
    deleted = retention_sweep(tmp_path, retention_days=7, mode="immediate")
    assert deleted == 2
    assert not f1.exists() and not f2.exists()
    assert keep.exists()


def test_retention_sweep_manual_noop(tmp_path: Path) -> None:
    f1 = _make_file(tmp_path / "tmp" / "a.bin", age_days=100)
    deleted = retention_sweep(tmp_path, retention_days=7, mode="manual")
    assert deleted == 0
    assert f1.exists()


def test_resolve_cleanup_config_defaults() -> None:
    assert resolve_cleanup_config(None) == (
        DEFAULT_RETENTION_DAYS, DEFAULT_CLEANUP_MODE
    )
    assert resolve_cleanup_config({"data": {}}) == (
        DEFAULT_RETENTION_DAYS, DEFAULT_CLEANUP_MODE
    )
    # 畸形值兜底
    assert resolve_cleanup_config(
        {"data": {"retentionDays": -3, "cleanupMode": "bogus"}}
    ) == (DEFAULT_RETENTION_DAYS, DEFAULT_CLEANUP_MODE)
    # 合法值透传
    assert resolve_cleanup_config(
        {"data": {"retentionDays": 3, "cleanupMode": "manual"}}
    ) == (3, "manual")


def test_run_retention_sweep_honors_workspace_settings(tmp_path: Path) -> None:
    """工作区 settings 配 cleanupMode=manual 时启动清扫跳过。"""
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    repos = Repos(conn)
    ws = repos.workspaces.ensure("ws-x")
    repos.workspaces.update_settings(
        ws.id, {"data": {"retentionDays": 0, "cleanupMode": "manual"}}
    )
    stale = _make_file(tmp_path / "tmp" / "stale.bin", age_days=100)
    assert run_retention_sweep(conn, home=tmp_path) == 0
    assert stale.exists()

    # 切回 scheduled 后清扫生效
    repos.workspaces.update_settings(
        ws.id, {"data": {"retentionDays": 7, "cleanupMode": "scheduled"}}
    )
    assert run_retention_sweep(conn, home=tmp_path) == 1
    assert not stale.exists()
