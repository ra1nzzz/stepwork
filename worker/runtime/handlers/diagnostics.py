"""诊断包导出 handler（W8 L.32）。

路由 ``ExportDiagnosticsBundle`` 命令，收集运行时诊断信息并打成 zip：

1. health 摘要（python_version / sqlite_version / platform / uptime / active_jobs）
2. DB schema 版本（从 ``schema_migrations`` 表读取已应用版本列表）
3. 配置快照（读 workspace settings；脱敏时复用 ``config._mask_secrets`` 掩码）
4. 最近 N 行 worker.log（若存在；W8 日志文件落盘为 P1 后置，不存在时返空 list）

安全模型（P0）：

- 默认 ``desensitize=True``（payload 可传 ``desensitize: false`` 覆盖，但默认场景必须脱敏）
- 复用 :func:`worker.runtime.handlers.config._mask_secrets` 做递归掩码
- bundle 内不含明文密钥（测试断言 grep ``apiKey`` / ``secret`` / ``token`` /
  ``password`` 等模式的值不能是明文）

日志文件策略：``$STEPWORK_HOME/logs/worker.log``（W8_PLAN D5 约定）。
W8 的 ``__main__._configure_logging`` 仅走 stderr，文件不存在时
``_collect_recent_logs`` 返空 list；日志文件 handler 落盘为 P1 后置改造。
"""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.handlers.config import _mask_secrets
from worker.runtime.models import CommandEnvelope, CommandResult

# worker.log 相对路径：$STEPWORK_HOME/logs/worker.log（W8_PLAN D5 约定）
_LOG_REL_PATH: Path = Path("logs") / "worker.log"

# 诊断包输出目录名（$STEPWORK_HOME/diagnostics/）
_DIAGNOSTICS_DIR: str = "diagnostics"

# 读取 worker.log 的默认最大行数
_DEFAULT_MAX_LOG_LINES: int = 200

# 活跃任务状态集合（用于 health 摘要的 active_jobs 计数）
_ACTIVE_JOB_STATES: tuple[str, ...] = ("pending", "leased", "running")


def _resolve_stepwork_home() -> Path:
    """解析 ``$STEPWORK_HOME``，缺省回退到 ``~/STEPWORK``（与 bootstrap.py 一致）。"""
    home = os.environ.get("STEPWORK_HOME") or str(Path.home() / "STEPWORK")
    return Path(home)


def _collect_health_summary(deps: Deps) -> dict[str, Any]:
    """收集 worker 运行时摘要。

    包含 python_version / sqlite_version / platform / uptime / active_jobs。
    ``uptime_seconds`` 需注入 ``WorkerState``（P1 改进），当前经 ``Deps`` 不可达，置 None。
    """
    conn = deps.repos.conn
    version_row = conn.execute("SELECT sqlite_version()").fetchone()
    sqlite_version = str(version_row[0]) if version_row is not None else "unknown"
    count_row = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE state IN (?, ?, ?)",
        _ACTIVE_JOB_STATES,
    ).fetchone()
    active_jobs = int(count_row[0]) if count_row is not None else 0
    return {
        "python_version": platform.python_version(),
        "sqlite_version": sqlite_version,
        "platform": platform.platform(),
        "uptime_seconds": None,  # P1: 需 WorkerState 注入
        "active_jobs": active_jobs,
    }


def _collect_db_schema_version(deps: Deps) -> dict[str, Any]:
    """从 ``schema_migrations`` 表读取已应用的迁移版本列表。"""
    conn = deps.repos.conn
    try:
        rows = conn.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.Error:
        return {"versions": [], "error": "schema_migrations table not accessible"}
    return {
        "versions": [
            {"version": str(row["version"]), "applied_at": str(row["applied_at"])}
            for row in rows
        ],
    }


def _collect_config_snapshot(deps: Deps, workspace_id: str) -> dict[str, Any]:
    """读 workspace settings，返配置快照。

    掩码由 ``_build_bundle`` 按 ``desensitize`` 统一应用。
    """
    ws = deps.repos.workspaces.ensure(workspace_id)
    return ws.settings or {}


def _collect_recent_logs(log_path: Path, max_lines: int = _DEFAULT_MAX_LOG_LINES) -> list[str]:
    """读最近 N 行 worker.log（文件不存在返空 list）。"""
    if not log_path.exists() or not log_path.is_file():
        return []
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    return [line.rstrip("\n") for line in lines[-max_lines:]]


def _build_bundle(contents: dict[str, Any], bundle_path: Path, desensitize: bool) -> Path:
    """把 contents 打成 zip（每个 key 一个 JSON/txt 文件）。

    若 ``desensitize=True``，先对 dict/list 类型的值递归掩码密钥字段
    （复用 :func:`config._mask_secrets`），确保 bundle 内无明文密钥。

    文件命名规则：

    - ``str`` → ``<key>.txt``
    - ``list[str]`` → ``<key>.txt``（每行一条）
    - 其余（dict / list[dict] / …）→ ``<key>.json``
    """
    if desensitize:
        safe_contents: dict[str, Any] = {
            k: _mask_secrets(v) if isinstance(v, (dict, list)) else v
            for k, v in contents.items()
        }
    else:
        safe_contents = contents

    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for key, value in safe_contents.items():
            safe_name = key.replace("/", "_").replace("\\", "_")
            if isinstance(value, str):
                zf.writestr(f"{safe_name}.txt", value)
            elif isinstance(value, list) and all(isinstance(v, str) for v in value):
                zf.writestr(f"{safe_name}.txt", "\n".join(value))
            else:
                zf.writestr(
                    f"{safe_name}.json",
                    json.dumps(value, ensure_ascii=False, indent=2),
                )
    return bundle_path


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由 ``ExportDiagnosticsBundle``：收集诊断信息并打成 zip。"""
    if env.commandType != "ExportDiagnosticsBundle":
        raise DispatchError(
            "UNKNOWN_COMMAND",
            f"commandType {env.commandType!r} not handled by diagnostics handler",
        )

    payload = env.payload or {}

    # 读 workspace（一次），解析脱敏开关
    config_snapshot = _collect_config_snapshot(deps, env.workspaceId)
    data_section = config_snapshot.get("data") if isinstance(config_snapshot, dict) else None
    config_desensitize = (
        bool(data_section.get("desensitize", True))
        if isinstance(data_section, dict)
        else True
    )
    # 优先级：payload > data.desensitize(config) > True(默认)
    desensitize = bool(payload.get("desensitize", config_desensitize))
    max_log_lines = int(payload.get("maxLogLines", _DEFAULT_MAX_LOG_LINES))

    home = _resolve_stepwork_home()
    log_path = home / _LOG_REL_PATH

    contents: dict[str, Any] = {
        "health": _collect_health_summary(deps),
        "db_schema": _collect_db_schema_version(deps),
        "config": config_snapshot,
        "worker_log": _collect_recent_logs(log_path, max_log_lines),
    }

    # 打 zip（_build_bundle 内按 desensitize 统一掩码）
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    bundle_path = home / _DIAGNOSTICS_DIR / f"stepwork-diagnostics-{timestamp}.zip"
    _build_bundle(contents, bundle_path, desensitize)

    size_bytes = bundle_path.stat().st_size
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        detail={
            "bundle_path": str(bundle_path),
            "size_bytes": size_bytes,
            "desensitized": desensitize,
            "contents": list(contents.keys()),
        },
    )
