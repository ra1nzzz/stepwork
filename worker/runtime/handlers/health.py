"""``runtime.health_check`` handler（v1.1 Patch-S6 / Patch-U3 / P1-架构-5）。"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from worker.runtime import __version__
from worker.runtime.state import WorkerState


class HealthStatus(BaseModel):
    """``runtime.health_check`` 响应载荷（v1.1 完整字段集）。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded", "down"]
    version: str
    protocol_version: str = "1"
    uptime_seconds: int
    pid: int
    last_heartbeat_at: str | None = None
    startup_duration_ms: int = 0
    active_jobs: int = 0
    degraded_reasons: list[str] = Field(default_factory=list)
    runtime_info: dict[str, Any] = Field(default_factory=dict)


def _sqlite_version_sync() -> str:
    """同步获取 SQLite 版本（供 ``asyncio.to_thread`` 包装）。

    Returns:
        ``sqlite3.sqlite_version`` 字符串。
    """
    return sqlite3.sqlite_version


async def handle_health_check(
    params: dict[str, Any] | None,
    state: WorkerState,
) -> HealthStatus:
    """处理 ``runtime.health_check``。

    Args:
        params: JSON-RPC 参数（当前未使用，保留扩展位）。
        state: Worker 运行期状态。

    Returns:
        :class:`HealthStatus`；``degraded_reasons`` 非空时 ``status=degraded``。
    """
    del params  # W1 未使用

    sqlite_version = await asyncio.to_thread(_sqlite_version_sync)

    status: Literal["ok", "degraded", "down"] = (
        "degraded" if state.degraded_reasons else "ok"
    )

    runtime_info: dict[str, Any] = {
        "python_version": sys.version.split()[0],
        "sqlite_version": sqlite_version,
        "platform": sys.platform,
    }

    return HealthStatus(
        status=status,
        version=__version__,
        protocol_version=state.protocol_version,
        uptime_seconds=state.uptime_seconds(),
        pid=state.pid,
        last_heartbeat_at=(
            state.last_heartbeat_at.isoformat() if state.last_heartbeat_at else None
        ),
        startup_duration_ms=state.startup_duration_ms,
        active_jobs=state.active_jobs,
        degraded_reasons=list(state.degraded_reasons),
        runtime_info=runtime_info,
    )
