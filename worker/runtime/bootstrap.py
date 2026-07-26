"""Worker 启动引导（W3-W4 Batch 0）。

- 打开 SQLite 连接（或在测试中直接注入内存/临时连接）
- 顺序应用 migrations/
- 启动任务恢复（T5）：``sweep_expired`` + 孤儿 RUNNING/LEASED 置 ``EXPIRED``
- 写入 ``state.db_conn`` / ``state.db_path`` 供 Command Bus 使用
- 生产路径下，迁移前自动备份 ``stepwork.db``（回滚策略见 migrations/README）
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import run_migrations
from worker.runtime.state import WorkerState

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

logger = logging.getLogger("worker.runtime")


def recover_orphan_jobs(conn: sqlite3.Connection) -> int:
    """启动孤儿任务恢复（T5）：RUNNING/LEASED 一律置 ``EXPIRED``。

    本 worker 是唯一执行者：进程启动时任何仍处于 ``RUNNING`` / ``LEASED``
    的 job 必然来自上一个已死进程（崩溃 / ``kill -9``），不可能还在跑。
    统一置 ``EXPIRED``（清空租约字段，``error_code='worker restarted'``），
    使 ``engine.retry_eligible`` 能看到并重新入队。

    Returns:
        被恢复（置 EXPIRED）的 job 行数。
    """
    from worker.runtime.models import JobState

    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "UPDATE jobs SET state=?, lease_owner=NULL, lease_expires_at=NULL, "
        "error_code=?, updated_at=? WHERE state IN (?, ?)",
        (
            JobState.EXPIRED.value,
            "worker restarted",
            now,
            JobState.RUNNING.value,
            JobState.LEASED.value,
        ),
    )
    conn.commit()
    return cur.rowcount


def _resolve_db_path(backup: bool = True) -> str:
    """解析数据库路径：``$STEPWORK_HOME/stepwork.db``，缺省落到用户主目录。

    ``backup=True`` 时自动备份现有数据库（回滚策略见 migrations/README）。
    备份失败不阻塞启动（仅记录到 stderr），避免权限问题导致 worker 崩溃。
    CLI/MCP 进程内门面每条命令都会 bootstrap，应传 ``backup=False``
    以免每条命令都整库复制一份备份（备份洪水）。
    """
    import logging
    import os

    logger = logging.getLogger("worker.runtime")
    home = os.environ.get("STEPWORK_HOME") or str(Path.home() / "STEPWORK")
    Path(home).mkdir(parents=True, exist_ok=True)
    db = Path(home) / "stepwork.db"
    if backup and db.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        backup = Path(home) / "backups" / f"stepwork-{stamp}.db"
        backup.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(db, backup)
        except OSError as exc:
            # 备份失败不阻塞启动（可能权限受限或磁盘满）
            logger.warning("启动备份失败（不阻塞启动）: %s", exc)
    return str(db)


def bootstrap_db(
    state: WorkerState,
    db_path: str | None = None,
    conn: Any = None,
    recover_jobs: bool = True,
    backup: bool = True,
) -> WorkerState:
    """初始化 Worker 的数据库层。

    Args:
        state: 运行期状态（回填 ``db_conn`` / ``db_path``）。
        db_path: 显式数据库路径；缺省解析自环境变量/主目录。
        conn: 测试注入的现成连接（优先，跳过文件打开与迁移文件查找）。
        recover_jobs: 迁移后执行启动任务恢复（T5：``sweep_expired`` +
            :func:`recover_orphan_jobs`）与 retention 清扫（PRD-SRC-005）。
            进程内门面（``app.run_command``）每条命令都会 bootstrap，且可能
            与正在跑长任务的桌面 worker 并存，应传 ``False``——既不能把
            在途 RUNNING/LEASED 误判为孤儿，也不能清掉并发 worker 正在写
            的 ``.part`` 下载中间文件。
        backup: 缺省路径解析时是否先备份现有数据库（透传
            :func:`_resolve_db_path`）。进程内门面应传 ``False``，避免
            每条命令整库复制一次的备份洪水；桌面 worker 路径保持 ``True``。

    Returns:
        同一 ``state`` 实例（便于链式）。
    """
    if conn is not None:
        state.db_conn = conn
        state.db_path = ":memory:"
        return state

    path = db_path or _resolve_db_path(backup=backup)
    connection = connect(path)
    run_migrations(connection, MIGRATIONS_DIR)
    if recover_jobs:
        # T5 启动恢复：先扫过期租约，再清孤儿 RUNNING/LEASED
        from worker.runtime.jobs.lease import sweep_expired

        swept = sweep_expired(connection)
        orphaned = recover_orphan_jobs(connection)
        if swept or orphaned:
            logger.info(
                "startup job recovery: swept=%s orphaned=%s", len(swept), orphaned
            )
        # Tranche 2（PRD-SRC-005）：启动清扫 temp/下载中间文件
        # （retentionDays + cleanupMode 策略；内部兜底，绝不阻塞启动）。
        # 仅桌面 worker 路径执行：per-command 门面清扫可能删掉并发
        # worker 正在写的 .part 中间文件。
        from worker.runtime.cleanup import run_retention_sweep

        run_retention_sweep(connection)
    state.db_conn = connection
    state.db_path = path
    return state
