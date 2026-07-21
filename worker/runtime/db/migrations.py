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
    files = sorted(Path(migrations_dir).glob("*.sql"))

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
