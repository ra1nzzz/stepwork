"""迁移回滚的往返测试。

写了 down 脚本但从没跑过 = 没有 down 脚本。真到升级失败那天才发现回滚脚本
本身有错，是最坏的情形 —— 那时用户的库已经停在半路了。

所以这里做的是**真往返**：升到最新 → 一步步回滚到零 → 再升回最新，
中途任何一步 SQL 有错都会当场炸。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import (
    _DOWN_SUFFIX,
    _up_files,
    rollback_migration,
    run_migrations,
)

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(r["name"]) for r in rows}


def test_every_migration_has_a_rollback_script() -> None:
    """每个 up 都必须配 down —— 少一个就断了整条回滚链。

    回滚是一步步往回退的：中间缺一个，它之前的所有迁移都变得无法回滚。
    """
    missing = []
    for up in _up_files(_MIG_DIR):
        version = up.stem.split("_", 1)[0]
        if not list(_MIG_DIR.glob(f"{version}_*{_DOWN_SUFFIX}")):
            missing.append(up.name)
    assert not missing, f"以下迁移缺少 .down.sql：{missing}"


def test_down_files_are_not_applied_as_up(tmp_path: Path) -> None:
    """``*.down.sql`` 绝不能被当成正向迁移执行。

    这不是假想：``0001_init.down.sql`` 在字典序上排在 ``0001_init.sql``
    **之前**（'d' < 's'）。若不排除，全新库上会先跑 down（DROP IF EXISTS
    什么也没删）、把 0001 标记为已应用，真正的建表脚本就被永久跳过 ——
    然后一切都以「no such table」失败。
    """
    conn = connect(str(tmp_path / "fresh.db"))
    try:
        run_migrations(conn, _MIG_DIR)
        tables = _tables(conn)
        # 若 down 被当成 up 跑过，这些核心表根本不会存在
        assert "workspaces" in tables
        assert "content_projects" in tables
        assert "content_versions" in tables
    finally:
        conn.close()


def test_full_round_trip(tmp_path: Path) -> None:
    """升到最新 → 全部回滚 → 再升回最新。

    第二次升级尤其关键：它验证 down 脚本**清理干净了**。若某个 down 漏删了
    一张表，重新 up 时 CREATE TABLE 会撞名而失败。
    """
    conn = connect(str(tmp_path / "rt.db"))
    try:
        applied = run_migrations(conn, _MIG_DIR)
        assert applied > 0
        peak = _tables(conn)
        assert "command_metrics" in peak

        # 一步步回滚到零
        rolled = []
        while True:
            version = rollback_migration(conn, _MIG_DIR)
            if version is None:
                break
            rolled.append(version)
        assert len(rolled) == applied, f"回滚步数与升级步数不符：{rolled}"
        # 回滚到零后只剩追踪表
        assert _tables(conn) <= {"schema_migrations"}

        # 再升回最新：这一步会暴露「down 漏删」——CREATE TABLE 会撞名
        again = run_migrations(conn, _MIG_DIR)
        assert again == applied
        assert _tables(conn) == peak
    finally:
        conn.close()


def test_rollback_is_one_step_at_a_time(tmp_path: Path) -> None:
    """一次只退一步：批量回滚一旦中途失败，库会停在谁也说不清的中间态。"""
    conn = connect(str(tmp_path / "step.db"))
    try:
        run_migrations(conn, _MIG_DIR)
        before = conn.execute("SELECT COUNT(*) n FROM schema_migrations").fetchone()["n"]
        version = rollback_migration(conn, _MIG_DIR)
        after = conn.execute("SELECT COUNT(*) n FROM schema_migrations").fetchone()["n"]
        assert version is not None
        assert after == before - 1
    finally:
        conn.close()


def test_rollback_on_empty_db_returns_none(tmp_path: Path) -> None:
    """没有可回滚的迁移时如实返回 None，而不是抛错。"""
    conn = connect(str(tmp_path / "empty.db"))
    try:
        assert rollback_migration(conn, _MIG_DIR) is None
    finally:
        conn.close()


def test_missing_down_script_raises(tmp_path: Path) -> None:
    """缺 down 脚本时明确报错，不能静默跳过。

    静默跳过意味着「以为回滚了、其实没有」，比直接失败危险得多。
    """
    mig = tmp_path / "migs"
    mig.mkdir()
    (mig / "0001_only_up.sql").write_text("CREATE TABLE t(id TEXT);", encoding="utf-8")
    conn = connect(str(tmp_path / "x.db"))
    try:
        run_migrations(conn, mig)
        with pytest.raises(RuntimeError, match="no .down.sql"):
            rollback_migration(conn, mig)
    finally:
        conn.close()
