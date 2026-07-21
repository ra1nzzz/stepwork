"""生命周期 handlers：``runtime.ready`` / ``runtime.heartbeat`` / ``runtime.shutdown``。

v1.1 Patch-A3：``runtime.ready`` 携带 ``protocol_version`` 与 ``capabilities``。
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict

from worker.runtime import __version__
from worker.runtime.state import WorkerState


class ShutdownParams(BaseModel):
    """``runtime.shutdown`` 请求参数。"""

    model_config = ConfigDict(extra="forbid")

    graceful: bool = True


async def handle_ready(state: WorkerState) -> dict[str, Any]:
    """构造 ``runtime.ready`` notification 的 params。

    Args:
        state: Worker 运行期状态。

    Returns:
        包含 ``ready/pid/started_at/protocol_version/capabilities/worker_version`` 的字典。
    """
    return {
        "ready": True,
        "pid": state.pid,
        "started_at": state.started_at.isoformat(),
        "protocol_version": state.protocol_version,
        "capabilities": list(state.capabilities),
        "worker_version": __version__,
    }


async def handle_heartbeat(state: WorkerState) -> dict[str, Any]:
    """构造 ``runtime.heartbeat`` notification 的 params，并更新 ``last_heartbeat_at``。

    Args:
        state: Worker 运行期状态。

    Returns:
        包含 ``alive/timestamp`` 的字典。
    """
    now = state.touch_heartbeat()
    return {
        "alive": True,
        "timestamp": now.isoformat(),
    }


async def handle_shutdown(
    params: dict[str, Any] | None,
    state: WorkerState,
    shutdown_event: asyncio.Event,
) -> dict[str, Any]:
    """处理 ``runtime.shutdown``：触发优雅退出。

    Args:
        params: JSON-RPC 参数（可含 ``graceful`` 标志）。
        state: Worker 运行期状态（当前仅记录，不修改）。
        shutdown_event: 由 ``__main__`` 创建的事件；设置后主循环退出。

    Returns:
        ``{"bye": True, "graceful": <bool>}``。
    """
    del state  # W1 不修改状态

    parsed = ShutdownParams.model_validate(params or {})
    shutdown_event.set()
    return {"bye": True, "graceful": parsed.graceful}
