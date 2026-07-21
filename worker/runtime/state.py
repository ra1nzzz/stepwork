"""Worker 运行时状态。

保存启动时间、session token、协议版本、心跳时间戳等运行期信息。
所有字段由 :class:`WorkerState` 集中管理，handler 通过依赖注入读取。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# v1.1 Patch-A3：协议版本协商
PROTOCOL_VERSION: str = "1"
"""JSON-RPC 协议版本。"""

# v1.1 Patch-A3：能力声明
DEFAULT_CAPABILITIES: list[str] = ["health", "heartbeat", "commands", "jobs"]
"""W1 Worker 声明的能力集。"""


def _utc_now() -> datetime:
    """返回当前 UTC 时间（timezone-aware）。

    Returns:
        带 ``UTC`` tzinfo 的 ``datetime``。
    """
    return datetime.now(UTC)


def _resolve_session_token() -> str:
    """从环境变量读取 session token；缺省时生成 uuid4 hex。

    Returns:
        32 字节 hex token。
    """
    token = os.environ.get("STEPWORK_SESSION_TOKEN")
    if token:
        return token
    return uuid.uuid4().hex


class WorkerState(BaseModel):
    """Worker 运行期状态（跨 handler 共享）。

    Attributes:
        started_at: 进程启动时间（UTC）。
        pid: 进程 ID。
        protocol_version: JSON-RPC 协议版本。
        capabilities: 能力清单。
        session_token: 会话 token（由 Tauri 注入或本地生成）。
        startup_duration_ms: 启动耗时（由 ``__main__`` 在发送 ready 前回填）。
        last_heartbeat_at: 最近一次心跳发送时间（UTC）。
        active_jobs: 当前活跃任务数（W1 恒为 0）。
        degraded_reasons: 降级原因列表（非空时 health.status=degraded）。
        monotonic_start: ``time.monotonic()`` 启动锚点（用于 uptime 计算）。
        shutdown_event: 优雅退出事件（由 ``__main__`` 主循环监听）。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    started_at: datetime = Field(default_factory=_utc_now)
    pid: int = Field(default_factory=os.getpid)
    protocol_version: str = PROTOCOL_VERSION
    capabilities: list[str] = Field(default_factory=lambda: list(DEFAULT_CAPABILITIES))
    session_token: str = Field(default_factory=_resolve_session_token)
    startup_duration_ms: int = 0
    last_heartbeat_at: datetime | None = None
    active_jobs: int = 0
    degraded_reasons: list[str] = Field(default_factory=list)
    monotonic_start: float = Field(default_factory=time.monotonic)
    shutdown_event: asyncio.Event = Field(default_factory=asyncio.Event)
    # W3-W4 Batch 0：数据库层（由 ``bootstrap.bootstrap_db`` 回填）
    db_conn: Any = None
    db_path: str | None = None

    def uptime_seconds(self) -> int:
        """计算自启动以来的整数秒（v1.1 Patch-S6）。

        Returns:
            ``int(time.monotonic() - monotonic_start)``。
        """
        return int(time.monotonic() - self.monotonic_start)

    def touch_heartbeat(self) -> datetime:
        """更新 ``last_heartbeat_at`` 为当前 UTC 时间。

        Returns:
            更新后的时间戳。
        """
        now = _utc_now()
        self.last_heartbeat_at = now
        return now
