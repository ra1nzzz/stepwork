"""迁移执行器（W3-W4 Batch 0）。

按 ``NNNN_*.sql`` 版本号升序应用 ``migrations/`` 下的 SQL 文件。
幂等：用一个 Python 侧维护的 ``schema_migrations`` 追踪表记录已应用版本，
已应用的文件跳过；重复调用安全。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

_TRACKING_TABLE: str = "schema_migrations"


def _ensure_tracking(conn: sqlite3.Connection) -> None:
    """建立版本追踪表（若不存在）。"""
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_TRACKING_TABLE} ("
        "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    conn.commit()


def _applied_versions(conn: sqlite3.Connection) -> set[str]:
    """返回已应用的迁移版本号集合。"""
    rows = conn.execute(f"SELECT version FROM {_TRACKING_TABLE}").fetchall()
    return {row["version"] for row in rows}


#: 回滚脚本的后缀。**必须**从 up 的文件列表里排除 —— ``0001_init.down.sql``
#: 在字典序上排在 ``0001_init.sql`` **之前**（'d' < 's'），若不排除，全新库
#: 上会先跑 down（DROP IF EXISTS 什么也没删）、把 0001 标记为已应用，真正的
#: 建表脚本就被永久跳过了。
_DOWN_SUFFIX = ".down.sql"


def _up_files(migrations_dir: str | Path) -> list[Path]:
    """正向迁移文件（排除 ``*.down.sql``）。"""
    return sorted(
        p for p in Path(migrations_dir).glob("*.sql") if not p.name.endswith(_DOWN_SUFFIX)
    )


def _down_file(migrations_dir: str | Path, version: str) -> Path | None:
    """某个版本对应的回滚脚本。"""
    for path in Path(migrations_dir).glob(f"{version}_*{_DOWN_SUFFIX}"):
        return path
    return None


def rollback_migration(conn: sqlite3.Connection, migrations_dir: str | Path) -> str | None:
    """回滚最近一次已应用的迁移；返回被回滚的版本号（无可回滚时 None）。

    只回滚**一步**：批量回滚看起来方便，但一旦中间某步失败，库会停在一个
    谁也说不清的中间态。一步一停，失败时至少知道停在哪。
    """
    _ensure_tracking(conn)
    row = conn.execute(
        f"SELECT version FROM {_TRACKING_TABLE} ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    version = str(row["version"])
    path = _down_file(migrations_dir, version)
    if path is None:
        raise RuntimeError(f"migration {version} has no .down.sql; cannot roll back")
    try:
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute(f"DELETE FROM {_TRACKING_TABLE} WHERE version=?", (version,))
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise RuntimeError(f"rollback of {version} failed: {exc}") from exc
    return version


def run_migrations(conn: sqlite3.Connection, migrations_dir: str | Path) -> int:
    """顺序应用未执行的迁移文件。

    Args:
        conn: 已打开的 SQLite 连接。
        migrations_dir: 含 ``NNNN_*.sql`` 的目录。

    Returns:
        本次新应用的迁移数量。

    Raises:
        RuntimeError: 单个迁移文件执行失败（已回滚）。
    """
    _ensure_tracking(conn)
    applied = _applied_versions(conn)
    files = _up_files(migrations_dir)

    applied_count = 0
    for path in files:
        version = path.stem.split("_", 1)[0]
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
            conn.execute(
                f"INSERT INTO {_TRACKING_TABLE}(version, applied_at) VALUES (?, ?)",
                (version, datetime.now(UTC).isoformat()),
            )
            conn.commit()
            applied_count += 1
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            conn.rollback()
            raise RuntimeError(f"migration {version} failed: {exc}") from exc
    return applied_count
