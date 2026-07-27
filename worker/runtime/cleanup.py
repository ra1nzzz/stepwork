"""临时文件清理（Tranche 2，PRD-SRC-005）。

清扫目标（下载/中间文件，不碰正式资产与产物）：

- ``$STEPWORK_HOME/tmp/`` 下的所有文件
- ``$STEPWORK_HOME/assets/`` 下残留的 ``*.part`` 下载中间文件

策略由 storage 配置（``data`` 配置节）决定：

- ``cleanupMode='manual'``：不清扫。
- ``cleanupMode='immediate'``：不论文件年龄全部清除（导入完成后
  即删中间文件；bootstrap 清扫同样立即清除残留）。
- ``cleanupMode='scheduled'``（默认）：仅清除超过 ``retentionDays``
  （mtime 判定）的文件。

入口 :func:`run_retention_sweep` 由 ``bootstrap_db`` 生产路径调用；
任何失败都降级为日志，绝不阻塞启动。
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("worker.runtime.cleanup")

DEFAULT_RETENTION_DAYS: int = 7
"""``retentionDays`` 缺省值（配置缺失时的兜底，PRD-SRC-005）。"""

DEFAULT_CLEANUP_MODE: str = "scheduled"
"""``cleanupMode`` 缺省值。"""

_VALID_MODES: tuple[str, ...] = ("immediate", "scheduled", "manual")


def resolve_stepwork_home() -> Path:
    """解析 ``$STEPWORK_HOME``，缺省回退到 ``~/STEPWORK``（与 bootstrap.py 一致）。"""
    home = os.environ.get("STEPWORK_HOME") or str(Path.home() / "STEPWORK")
    return Path(home)


def assets_root(home: Path | None = None) -> Path:
    """资产目录根：``$STEPWORK_HOME/assets``（URL 下载与文件删除的边界）。"""
    return (home or resolve_stepwork_home()) / "assets"


def resolve_cleanup_config(settings: dict[str, Any] | None) -> tuple[int, str]:
    """从工作区 settings 解析 ``(retentionDays, cleanupMode)``。

    settings 形状与 ``handlers.config`` 的 ``data`` 配置节一致；字段缺失 /
    畸形时回退默认（retentionDays=7、cleanupMode='scheduled'）。
    """
    data = (settings or {}).get("data")
    data = data if isinstance(data, dict) else {}
    retention = data.get("retentionDays", DEFAULT_RETENTION_DAYS)
    if not isinstance(retention, int) or isinstance(retention, bool) or retention < 0:
        retention = DEFAULT_RETENTION_DAYS
    mode = data.get("cleanupMode", DEFAULT_CLEANUP_MODE)
    if not isinstance(mode, str) or mode not in _VALID_MODES:
        mode = DEFAULT_CLEANUP_MODE
    return retention, mode


def _sweep_files(paths: list[Path], cutoff_ts: float | None) -> int:
    """删除文件列表中（可选）早于 ``cutoff_ts`` 的文件；返回删除数。"""
    deleted = 0
    for path in paths:
        try:
            if not path.is_file():
                continue
            if cutoff_ts is not None and path.stat().st_mtime > cutoff_ts:
                continue
            path.unlink()
            deleted += 1
        except OSError:
            # 单文件失败不影响其余（可能被占用/权限受限）
            continue
    return deleted


def retention_sweep(
    home: Path, retention_days: int, mode: str
) -> int:
    """按策略清扫 temp/下载中间文件；返回删除文件数。"""
    if mode == "manual":
        return 0
    cutoff: float | None
    if mode == "immediate":
        cutoff = None  # 不论年龄全部清除
    else:  # scheduled
        cutoff = time.time() - retention_days * 86400
    targets: list[Path] = []
    tmp_dir = home / "tmp"
    if tmp_dir.is_dir():
        targets.extend(p for p in tmp_dir.rglob("*") if p.is_file())
    assets_dir = assets_root(home)
    if assets_dir.is_dir():
        targets.extend(assets_dir.rglob("*.part"))
    return _sweep_files(targets, cutoff)


def run_retention_sweep(conn: Any, home: Path | None = None) -> int:
    """bootstrap 入口：读首个工作区的 storage 配置并执行清扫。

    无工作区行时按默认配置执行；任何异常都被吞掉并降级为日志
    （启动清扫绝不阻塞 worker）。
    """
    import json

    resolved_home = home or resolve_stepwork_home()
    try:
        settings: dict[str, Any] = {}
        row = conn.execute(
            "SELECT settings FROM workspaces ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is not None and row["settings"]:
            try:
                parsed = json.loads(row["settings"])
                settings = parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError):
                settings = {}
        retention_days, mode = resolve_cleanup_config(settings)
        deleted = retention_sweep(resolved_home, retention_days, mode)
        if deleted:
            logger.info(
                "retention sweep: deleted=%s mode=%s retention_days=%s",
                deleted, mode, retention_days,
            )
        return deleted
    except Exception:  # noqa: BLE001 - 启动清扫绝不阻塞 worker
        logger.warning("retention sweep failed (non-blocking)", exc_info=True)
        return 0
