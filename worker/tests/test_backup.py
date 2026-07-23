"""W9 L.40：备份恢复 handler 测试。

覆盖：

1. ``test_backup_workspace_creates_backup_file``：建 deps → ImportSource → BackupWorkspace；
   断言 backup_path 存在、size_bytes>0、source_db 含 stepwork.db
2. ``test_backup_workspace_with_label``：label="pre-migration"；断言文件名含 ``-pre-migration``
3. ``test_backup_workspace_label_sanitized``：label="evil/../path"；断言文件名含
   ``evil_.._path``（非法字符替换为 _）
4. ``test_restore_workspace_roundtrip``：建 deps + 写数据 A → BackupWorkspace → 写数据 B
   → RestoreWorkspace → 断言查询只看到 A 的数据（B 被覆盖）
5. ``test_restore_workspace_rejects_outside_backups_dir``：构造 backups/ 外的 .db 文件
   → DispatchError FORBIDDEN
6. ``test_restore_workspace_rejects_nonexistent``：backupPath 不存在 → DispatchError NOT_FOUND
7. ``test_restore_workspace_rejects_non_db``：backupPath 是 .txt → DispatchError INVALID_ARGUMENT
8. ``test_backup_workspace_db_not_found``：STEPWORK_HOME 指向空目录（无 stepwork.db）
   → DispatchError NOT_FOUND

参考 ``worker/tests/test_project_io.py`` 的 ``_envelope`` 风格，
``STEPWORK_HOME`` 指向 ``tmp_path`` 避免污染真实家目录；用真实文件 DB
（非 in_memory），因为备份恢复是文件级操作。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import connect, in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _path_exists(path_str: str) -> bool:
    """同步 helper：检查路径存在性（避免 async 测试函数内触发 ASYNC240）。"""
    return Path(path_str).exists()


def _deps_with_real_db(home: Path) -> Deps:
    """构造真实文件 DB + 迁移 + Deps（ingest 注入以便 ImportSource 计算 hash）。"""
    db_path = home / "stepwork.db"
    conn = connect(str(db_path))
    run_migrations(conn, _MIG_DIR)
    return Deps(repos=Repos(conn), ingest=ingest, asr=None, ai=None)


def _envelope(
    command_type: str,
    payload: dict[str, Any],
    workspace_id: str = "ws-1",
) -> dict[str, Any]:
    """构造最小合规命令信封 dict（对齐 command-envelope.schema.json）。"""
    return {
        "commandId": "cmd-1",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": workspace_id,
        "payload": payload,
        "requestedAt": "2026-07-24T00:00:00+00:00",
    }


async def test_backup_workspace_creates_backup_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """建 deps → ImportSource → BackupWorkspace；断言备份文件存在且元数据正确。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps_with_real_db(tmp_path)
    try:
        # 写入数据（ImportSource 建 project + asset）
        imp_res = await dispatch(
            _envelope(
                "ImportSource",
                {
                    "local_uri": "file://a.mp4",
                    "content_hash": "h_backup_test",
                    "kind": "video",
                },
            ),
            deps,
        )
        assert imp_res["ok"] is True

        res = await dispatch(_envelope("BackupWorkspace", {}), deps)
        assert res["ok"] is True
        detail = res["detail"]
        backup_path = Path(detail["backup_path"])
        assert _path_exists(str(backup_path))
        assert detail["size_bytes"] > 0
        assert "stepwork.db" in detail["source_db"]
    finally:
        deps.repos.conn.close()


async def test_backup_workspace_with_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """label="pre-migration"；断言文件名含 ``-pre-migration``。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps_with_real_db(tmp_path)
    try:
        res = await dispatch(
            _envelope("BackupWorkspace", {"label": "pre-migration"}),
            deps,
        )
        assert res["ok"] is True
        backup_path = Path(res["detail"]["backup_path"])
        assert _path_exists(str(backup_path))
        assert "-pre-migration" in backup_path.name
    finally:
        deps.repos.conn.close()


async def test_backup_workspace_label_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """label="evil/../path"；断言文件名含 ``evil_.._path``（非法字符替换为 _）。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps_with_real_db(tmp_path)
    try:
        res = await dispatch(
            _envelope("BackupWorkspace", {"label": "evil/../path"}),
            deps,
        )
        assert res["ok"] is True
        backup_path = Path(res["detail"]["backup_path"])
        assert _path_exists(str(backup_path))
        assert "evil_.._path" in backup_path.name
    finally:
        deps.repos.conn.close()


async def test_restore_workspace_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """建 deps + 写数据 A → BackupWorkspace → 写数据 B → RestoreWorkspace → 断言只看到 A。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps_with_real_db(tmp_path)

    # 写入数据 A
    await dispatch(
        _envelope(
            "ImportSource",
            {
                "local_uri": "file://a.mp4",
                "content_hash": "h_roundtrip_A",
                "kind": "video",
            },
        ),
        deps,
    )

    # 备份（此时 DB 只有 A）
    backup_res = await dispatch(_envelope("BackupWorkspace", {}), deps)
    assert backup_res["ok"] is True
    backup_path = backup_res["detail"]["backup_path"]

    # 写入数据 B（不同 content_hash，去重不会触发）
    await dispatch(
        _envelope(
            "ImportSource",
            {
                "local_uri": "file://b.mp4",
                "content_hash": "h_roundtrip_B",
                "kind": "video",
            },
        ),
        deps,
    )
    # 此时 DB 有 2 条 source_assets
    count_before = deps.repos.conn.execute(
        "SELECT COUNT(*) FROM source_assets"
    ).fetchone()
    assert count_before is not None
    assert int(count_before[0]) == 2

    # 恢复（覆盖回只有 A 的状态）
    restore_res = await dispatch(
        _envelope("RestoreWorkspace", {"backupPath": backup_path}),
        deps,
    )
    assert restore_res["ok"] is True
    detail = restore_res["detail"]
    assert detail["restored_to"].endswith("stepwork.db")
    assert detail["size_bytes"] > 0

    # 恢复后 DB 只剩 A 的数据（B 被覆盖）；
    # _rebind_conn 后 deps.repos.conn 已指向新连接
    count_after = deps.repos.conn.execute(
        "SELECT COUNT(*) FROM source_assets"
    ).fetchone()
    assert count_after is not None
    assert int(count_after[0]) == 1
    row = deps.repos.conn.execute(
        "SELECT content_hash FROM source_assets"
    ).fetchone()
    assert row is not None
    assert str(row["content_hash"]) == "h_roundtrip_A"

    # 清理：恢复后新连接需要关闭
    deps.repos.conn.close()


async def test_restore_workspace_rejects_outside_backups_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """构造一个在 backups/ 外的 .db 文件 → DispatchError FORBIDDEN。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps_with_real_db(tmp_path)
    try:
        # 在 backups/ 外构造一个 .db 文件
        outside_db = tmp_path / "outside.db"
        outside_db.write_bytes(b"SQLite format 3\x00")
        res = await dispatch(
            _envelope("RestoreWorkspace", {"backupPath": str(outside_db)}),
            deps,
        )
        assert res["ok"] is False
        assert "FORBIDDEN" in res["error"]
    finally:
        deps.repos.conn.close()


async def test_restore_workspace_rejects_nonexistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """backupPath 不存在 → DispatchError NOT_FOUND。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps_with_real_db(tmp_path)
    try:
        # backups/ 目录下但文件不存在
        nonexistent = tmp_path / "backups" / "stepwork-nonexistent.db"
        res = await dispatch(
            _envelope("RestoreWorkspace", {"backupPath": str(nonexistent)}),
            deps,
        )
        assert res["ok"] is False
        assert "NOT_FOUND" in res["error"]
    finally:
        deps.repos.conn.close()


async def test_restore_workspace_rejects_non_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """backupPath 是 .txt → DispatchError INVALID_ARGUMENT。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps_with_real_db(tmp_path)
    try:
        # 在 backups/ 下构造一个 .txt 文件
        backups_dir = tmp_path / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        txt_file = backups_dir / "not-a-db.txt"
        txt_file.write_text("hello")
        res = await dispatch(
            _envelope("RestoreWorkspace", {"backupPath": str(txt_file)}),
            deps,
        )
        assert res["ok"] is False
        assert "INVALID_ARGUMENT" in res["error"]
    finally:
        deps.repos.conn.close()


async def test_backup_workspace_db_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STEPWORK_HOME 指向空目录（无 stepwork.db）→ DispatchError NOT_FOUND。"""
    # tmp_path 此时是空目录（不构造 stepwork.db 文件）
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    # 用 in_memory 构造 deps（不创建 stepwork.db 文件）
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    deps = Deps(repos=Repos(conn), ingest=ingest, asr=None, ai=None)
    try:
        res = await dispatch(_envelope("BackupWorkspace", {}), deps)
        assert res["ok"] is False
        assert "NOT_FOUND" in res["error"]
    finally:
        conn.close()
