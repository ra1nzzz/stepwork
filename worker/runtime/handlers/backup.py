"""备份恢复 handler（W9 L.40）。

.. warning:: **命令名承诺"工作区级"，实现是"全局级"**（review 抓到的 P2）

   ``BackupWorkspace`` / ``RestoreWorkspace`` 操作的是**整个**
   ``$STEPWORK_HOME/stepwork.db`` 与相关 sidecar —— 单库多工作区架构下
   **不存在**按工作区物理拆分的边界，"只备份一个工作区"当前不可实现。
   detail 里回显 ``scope="global"`` 让调用方明确感知，命名保留是因为
   前端 / CLI / MCP 命令面 40+ 处已稳定依赖这个名字；未来若要真按
   工作区备份，需要一次数据层拆分（每个 workspace 一个 .db 或用
   ``ATTACH`` + 表级 filter），届时再连同命令语义一起改。

路由两个命令：

- ``BackupWorkspace``：把 ``$STEPWORK_HOME/stepwork.db`` 复制到
  ``$STEPWORK_HOME/backups/stepwork-<ts>[-label].db``，保留元数据（``shutil.copy2``）。
- ``RestoreWorkspace``：从指定备份文件恢复 ``stepwork.db``，关闭旧连接、
  复制文件、重新打开连接并 rebind 到 ``Repos`` 所有子 repo；同时回写
  ``deps.worker_state.db_conn``（T2 修复：``handlers.commands.handle_command``
  每次 dispatch 都从 ``state.db_conn`` 重建 ``Repos``，只 rebind 本次请求的
  ``Repos`` 会让恢复后的下一条命令拿到已关闭的旧连接而崩溃）。

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

import asyncio
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from worker.runtime.cleanup import resolve_stepwork_home
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



def _sanitize_label(label: str) -> str:
    """把 label 中的非法字符替换为 ``_``（仅允许 ``[a-zA-Z0-9_.-]``）。"""
    return _LABEL_SAFE_RE.sub("_", label)


def _rebind_conn(repos: Repos, new_conn: sqlite3.Connection) -> None:
    """把新连接绑定到 ``Repos`` 聚合对象及其所有子 repo。

    委托给 :meth:`Repos.rebind`：它动态遍历 ``vars(self)`` 把所有携带 ``.conn``
    的子 repo 一并重绑。历史上这里手写 5 个子 repo 的赋值，漏了
    ``video_scenes`` 与 ``hotspots`` —— RestoreWorkspace 关掉旧连接后，S2
    配音与 S5 热点发现会一路抛 ``ProgrammingError: Cannot operate on a
    closed database`` 直到重启。改由 Repos 自己负责同步后，新增子 repo
    无须回来改这里，也不会再出现「手工清单漏一项」。
    """
    repos.rebind(new_conn)


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


def _do_backup_copy(db_path: Path, backup_path: Path) -> int:
    """sync helper：复制 .db 快照并返回落地文件大小（放线程池里跑）。

    ``shutil.copy2`` 与 ``stat`` 都是**同步阻塞 IO** —— 生产 DB 可达 GB 级
    （素材表 + 渲染产物索引），整段拷贝跑在事件循环里会冻结心跳 / CancelJob /
    并发命令。文件级 copy 只能进 ``asyncio.to_thread``。
    """
    shutil.copy2(db_path, backup_path)
    return backup_path.stat().st_size


async def _handle_backup(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``BackupWorkspace``：复制 stepwork.db 到 backups/ 目录。"""
    payload = env.payload or {}
    label = payload.get("label")

    home = resolve_stepwork_home()
    db_path = home / _DB_FILENAME
    if not await asyncio.to_thread(db_path.exists):
        raise DispatchError(
            "NOT_FOUND", f"stepwork.db not found at {db_path}"
        )

    backups_dir = home / _BACKUPS_DIR
    await asyncio.to_thread(backups_dir.mkdir, parents=True, exist_ok=True)

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
    size_bytes = await asyncio.to_thread(_do_backup_copy, db_path, backup_path)
    created_at = datetime.now(UTC).isoformat()

    return CommandResult(
        ok=True,
        commandId=env.commandId,
        detail={
            "backup_path": str(backup_path),
            "size_bytes": size_bytes,
            "source_db": str(db_path),
            # 命令叫 BackupWorkspace 但动作是全局的（单库架构无工作区级
            # 物理边界）—— 显式回显 scope，前端 tooltip / CLI 输出可据此提示
            "scope": "global",
            "created_at": created_at,
        },
    )


async def _handle_restore(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``RestoreWorkspace``：从备份文件恢复 stepwork.db。"""
    payload = env.payload or {}
    backup_path_str = payload.get("backupPath") or payload.get("backup_path")
    if not backup_path_str:
        raise DispatchError("INVALID_ARGUMENT", "missing backupPath")

    home = resolve_stepwork_home()
    backup_path = _validate_backup_path(str(backup_path_str), home)

    db_path = home / _DB_FILENAME
    # 关闭旧连接（避免 Windows 上文件锁冲突；最后一连接关闭时 SQLite 自动 checkpoint）
    deps.repos.conn.close()
    # 清理残留 WAL/SHM sidecar + 整库复制 + stat —— 三条**同步阻塞 IO**，
    # DB 大时能跑几十秒，直接放事件循环里 = 心跳停 / CancelJob 排队 /
    # 并发命令全被卡住。丢线程池。
    await asyncio.to_thread(_cleanup_wal_sidecars, db_path)
    size_bytes = await asyncio.to_thread(_do_backup_copy, backup_path, db_path)
    # 重新打开连接并 rebind 到 Repos（含所有子 repo）
    new_conn = connect(str(db_path))
    _rebind_conn(deps.repos, new_conn)
    # T2 修复：worker 常驻态下，state.db_conn 是后续所有 dispatch 的连接源，
    # 必须一并替换，否则 RestoreWorkspace 之后的任何命令都会拿到已关闭连接
    if deps.worker_state is not None:
        deps.worker_state.db_conn = new_conn

    return CommandResult(
        ok=True,
        commandId=env.commandId,
        detail={
            "restored_to": str(db_path),
            "scope": "global",
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
