"""Worker 启动引导（W3-W4 Batch 0）。

- 打开 SQLite 连接（或在测试中直接注入内存/临时连接）
- 顺序应用 migrations/
- 写入 ``state.db_conn`` / ``state.db_path`` 供 Command Bus 使用
- 生产路径下，迁移前自动备份 ``stepwork.db``（回滚策略见 migrations/README）
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import run_migrations
from worker.runtime.state import WorkerState

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _resolve_db_path() -> str:
    """解析数据库路径：``$STEPWORK_HOME/stepwork.db``，缺省落到用户主目录。"""
    import os

    home = os.environ.get("STEPWORK_HOME") or str(Path.home() / "STEPWORK")
    Path(home).mkdir(parents=True, exist_ok=True)
    db = Path(home) / "stepwork.db"
    if db.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        backup = Path(home) / "backups" / f"stepwork-{stamp}.db"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(db, backup)
    return str(db)


def bootstrap_db(
    state: WorkerState,
    db_path: str | None = None,
    conn: Any = None,
) -> WorkerState:
    """初始化 Worker 的数据库层。

    Args:
        state: 运行期状态（回填 ``db_conn`` / ``db_path``）。
        db_path: 显式数据库路径；缺省解析自环境变量/主目录。
        conn: 测试注入的现成连接（优先，跳过文件打开与迁移文件查找）。

    Returns:
        同一 ``state`` 实例（便于链式）。
    """
    if conn is not None:
        state.db_conn = conn
        state.db_path = ":memory:"
        return state

    path = db_path or _resolve_db_path()
    connection = connect(path)
    run_migrations(connection, MIGRATIONS_DIR)
    state.db_conn = connection
    state.db_path = path
    return state
