"""SQLite 连接工厂（W3-W4 Batch 0）。

约定：
- 启用 WAL 日志模式（migrations/0001 亦声明，二者一致）。
- 启用外键约束（``foreign_keys=ON``）。
- ``row_factory = sqlite3.Row``，便于按列名取值。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开一个生产用 SQLite 连接。

    Args:
        db_path: 数据库文件路径。

    Returns:
        已配置（WAL + 外键 + Row factory）的连接。
    """
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # WAL 模式官方推荐 synchronous=NORMAL：每次 commit 不再 fsync，
    # fsync 交给 checkpoint（每 ~1000 页 / 关停）。进程崩溃最多回退
    # 到最后一次 checkpoint 之前，**库文件不会损坏**。本项目每条命令
    # 除业务写外还会 INSERT 一行 command_metrics 并 commit（observability
    # 的代价），FULL 下等于每次动作至少 2 次 fsync，SSD 上也明显；NORMAL
    # 是"本地创作工作台 + 桌面崩溃可接受"这类语义的正解。
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def in_memory() -> sqlite3.Connection:
    """打开一个内存库（测试用）。

    Returns:
        内存 SQLite 连接（外键开启）。
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn
