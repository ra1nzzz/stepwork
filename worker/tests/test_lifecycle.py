"""``worker.runtime.handlers.lifecycle`` 单元测试（3 用例）。

覆盖 v1.1 Patch-A3 协议版本协商与生命周期核心行为：

1. ``handle_ready`` 载荷完整（protocol_version/capabilities/worker_version）
2. ``handle_heartbeat`` 更新 ``last_heartbeat_at``
3. ``handle_shutdown`` 触发 ``state.shutdown_event``
"""

from __future__ import annotations

import asyncio

from worker.runtime import __version__
from worker.runtime.handlers import lifecycle
from worker.runtime.state import WorkerState


async def test_ready_contains_protocol_and_capabilities(
    worker_state: WorkerState,
) -> None:
    """``runtime.ready`` 载荷必须包含 ``protocol_version="1"`` 与 W1 capabilities。"""
    payload = await lifecycle.handle_ready(worker_state)

    assert payload["ready"] is True
    assert payload["pid"] == worker_state.pid
    assert payload["protocol_version"] == "1"
    assert payload["capabilities"] == ["health", "heartbeat", "commands", "jobs"]
    assert payload["worker_version"] == __version__
    assert isinstance(payload["started_at"], str)
    assert "T" in payload["started_at"]


async def test_heartbeat_updates_last_heartbeat_at(worker_state: WorkerState) -> None:
    """调用 ``handle_heartbeat`` 后 ``state.last_heartbeat_at`` 被刷新。"""
    assert worker_state.last_heartbeat_at is None

    payload = await lifecycle.handle_heartbeat(worker_state)

    assert payload["alive"] is True
    assert "timestamp" in payload
    assert worker_state.last_heartbeat_at is not None
    assert payload["timestamp"] == worker_state.last_heartbeat_at.isoformat()


async def test_shutdown_sets_state_shutdown_event(worker_state: WorkerState) -> None:
    """``handle_shutdown`` 触发 ``state.shutdown_event`` 并返回 ``{"bye": True}``。"""
    event = worker_state.shutdown_event
    assert isinstance(event, asyncio.Event)
    assert not event.is_set()

    result = await lifecycle.handle_shutdown({"graceful": True}, worker_state, event)

    assert result["bye"] is True
    assert result["graceful"] is True
    assert event.is_set()
