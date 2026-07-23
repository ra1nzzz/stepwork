"""备份恢复 handler（W9 L.40）。

路由两个命令：

- ``BackupWorkspace``：把 ``$STEPWORK_HOME/stepwork.db`` 复制到
  ``$STEPWORK_HOME/backups/stepwork-<ts>[-label].db``，保留元数据（``shutil.copy2``）。
- ``RestoreWorkspace``：从指定备份文件恢复 ``stepwork.db``，关闭旧连接、
  复制文件、重新打开连接并 rebind 到 ``Repos`` 所有子 repo。

安全模型（P0 R3）：

- ``RestoreWorkspace`` 校验 ``backupPath`` 必须在 ``$STEPWORK_HOME/backups/``
  目录下（``Path.resolve()`` + ``is_relative_to()`` 检查，防任意文件拷贝）。
- ``backupPath`` 必须存在且为 ``.db`` 文件。
- ``BackupWorkspace`` 的 ``label`` 仅允许 ``[a-zA-Z0-9_.-]``（``/`` 路径分隔符
  替换为 ``_``，``.`` 保留），避免文件名注入 / 路径穿越。

备份目录约定与 :func:`worker.runtime.bootstrap._resolve_db_path` 一致
（``$STEPWORK_HOME/backups/stepwork-<ts>.db``）。
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from worker.runtime.commands.bus import DispatchError
from worker.runtime.db.connection import connect
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult

# 备份目录名（$STEPWORK_HOME/backups/，与 bootstrap._resolve_db_path 一致）
_BACKUPS_DIR: str = "backups"

# stepwork.db 文件名
_DB_FILENAME: str = "stepwork.db"

# label 合法字符集：允许 [a-zA-Z0-9_.-]（含 .，与 W9_PLAN §8 测试用例一致：
# "evil/../path" → "evil_.._path"，仅 / 替换为 _，. 保留）
# / 是路径分隔符必须替换；. 在文件名中无害，保留可读性
_LABEL_SAFE_RE: re.Pattern[str] = re.compile(r"[^a-zA-Z0-9_.-]")


def _resolve_stepwork_home() -> Path:
    """解析 ``$STEPWORK_HOME``，缺省回退到 ``~/STEPWORK``（与 bootstrap.py 一致）。"""
    home = os.environ.get("STEPWORK_HOME") or str(Path.home() / "STEPWORK")
    return Path(home)


def _sanitize_label(label: str) -> str:
    """把 label 中的非法字符替换为 ``_``（仅允许 ``[a-zA-Z0-9_.-]``）。"""
    return _LABEL_SAFE_RE.sub("_", label)


def _rebind_conn(repos: Repos, new_conn: sqlite3.Connection) -> None:
    """把新连接绑定到 ``Repos`` 聚合对象及其所有子 repo。

    ``Repos.__init__`` 把 ``conn`` 直接引用赋给各子 repo 的 ``self.conn``，
    故重连后需要遍历所有子 repo 把它们的 ``.conn`` 也指向新连接，否则
    调用 ``repos.projects.*`` 等仍会使用已关闭的旧连接。
    """
    repos.conn = new_conn
    repos.workspaces.conn = new_conn
    repos.projects.conn = new_conn
    repos.source_assets.conn = new_conn
    repos.jobs.conn = new_conn
    repos.content_versions.conn = new_conn


def _validate_backup_path(backup_path_str: str, home: Path) -> Path:
    """校验 backupPath：必须在 backups/ 下、存在且为 .db 文件。

    同步 helper（与 project_io._check_bundle_path 同模式），把 ``Path`` 阻塞
    I/O 集中在 sync 函数内，避免 async handler 触发 ASYNC240。
    """
    backups_dir_resolved = (home / _BACKUPS_DIR).resolve()
    backup_path = Path(backup_path_str).resolve()

    # 安全检查（P0 R3）：backupPath 必须在 $STEPWORK_HOME/backups/ 目录下
    if not backup_path.is_relative_to(backups_dir_resolved):
        raise DispatchError(
            "FORBIDDEN",
            "backupPath must be under $STEPWORK_HOME/backups/",
        )
    if not backup_path.exists() or not backup_path.is_file():
        raise DispatchError(
            "NOT_FOUND", f"backup not found: {backup_path_str}"
        )
    if backup_path.suffix.lower() != ".db":
        raise DispatchError(
            "INVALID_ARGUMENT", f"backup must be a .db file: {backup_path_str}"
        )
    return backup_path


def _cleanup_wal_sidecars(db_path: Path) -> None:
    """删除 SQLite WAL 模式的 sidecar 文件（``-wal`` / ``-shm``）。

    恢复前旧连接已关闭，但 Windows 上 WAL/SHM 文件未必被自动删除；
    若残留，新复制的 DB 可能被旧 WAL 状态污染，故显式清理。
    """
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.parent / (db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


async def _handle_backup(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``BackupWorkspace``：复制 stepwork.db 到 backups/ 目录。"""
    payload = env.payload or {}
    label = payload.get("label")

    home = _resolve_stepwork_home()
    db_path = home / _DB_FILENAME
    if not db_path.exists():
        raise DispatchError(
            "NOT_FOUND", f"stepwork.db not found at {db_path}"
        )

    backups_dir = home / _BACKUPS_DIR
    backups_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if label:
        safe_label = _sanitize_label(str(label))
        backup_name = f"stepwork-{timestamp}-{safe_label}.db"
    else:
        backup_name = f"stepwork-{timestamp}.db"
    backup_path = backups_dir / backup_name

    # WAL 模式下，先 checkpoint 把 WAL 数据刷到主 DB 文件，确保 shutil.copy2
    # 拿到的是完整快照（否则只拷主 .db 文件会丢失尚未 checkpoint 的事务）
    deps.repos.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(db_path, backup_path)
    size_bytes = backup_path.stat().st_size
    created_at = datetime.now(UTC).isoformat()

    return CommandResult(
        ok=True,
        commandId=env.commandId,
        detail={
            "backup_path": str(backup_path),
            "size_bytes": size_bytes,
            "source_db": str(db_path),
            "created_at": created_at,
        },
    )


async def _handle_restore(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``RestoreWorkspace``：从备份文件恢复 stepwork.db。"""
    payload = env.payload or {}
    backup_path_str = payload.get("backupPath") or payload.get("backup_path")
    if not backup_path_str:
        raise DispatchError("INVALID_ARGUMENT", "missing backupPath")

    home = _resolve_stepwork_home()
    backup_path = _validate_backup_path(str(backup_path_str), home)

    db_path = home / _DB_FILENAME
    # 关闭旧连接（避免 Windows 上文件锁冲突；最后一连接关闭时 SQLite 自动 checkpoint）
    deps.repos.conn.close()
    # 清理可能残留的 WAL/SHM sidecar 文件（避免与新复制的 DB 冲突）
    _cleanup_wal_sidecars(db_path)
    # 复制备份文件到 stepwork.db（覆盖）
    shutil.copy2(backup_path, db_path)
    # 重新打开连接并 rebind 到 Repos（含所有子 repo）
    new_conn = connect(str(db_path))
    _rebind_conn(deps.repos, new_conn)

    size_bytes = db_path.stat().st_size
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        detail={
            "restored_to": str(db_path),
            "size_bytes": size_bytes,
            "backup_path": str(backup_path),
        },
    )


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由 ``BackupWorkspace`` / ``RestoreWorkspace`` 两个命令。"""
    if env.commandType == "BackupWorkspace":
        return await _handle_backup(env, deps)
    if env.commandType == "RestoreWorkspace":
        return await _handle_restore(env, deps)
    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by backup handler",
    )
