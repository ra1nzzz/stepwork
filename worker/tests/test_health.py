"""``worker.runtime.handlers.health`` 单元测试（4 用例）。

覆盖 v1.1 Patch-S6 / Patch-U3 / P1-架构-5 要求的核心行为：

1. v1.1 字段齐全
2. ``uptime_seconds`` 为 ``int`` 且随 ``time.monotonic()`` 递增
3. ``runtime_info`` 含 ``python_version`` / ``sqlite_version`` / ``platform``
4. ``degraded_reasons`` 非空 → ``status=degraded``
"""

from __future__ import annotations

import sys
import time

from worker.runtime import __version__
from worker.runtime.handlers.health import handle_health_check
from worker.runtime.state import WorkerState


async def test_all_v1_1_fields_present(worker_state: WorkerState) -> None:
    """v1.1 字段（pid/last_heartbeat_at/startup_duration_ms/"""
    """protocol_version/runtime_info）全部存在。"""
    result = await handle_health_check({}, worker_state)
    dumped = result.model_dump()

    assert dumped["status"] == "ok"
    assert dumped["version"] == __version__
    assert dumped["protocol_version"] == "1"
    assert dumped["pid"] == worker_state.pid
    assert dumped["startup_duration_ms"] == worker_state.startup_duration_ms
    assert dumped["active_jobs"] == 0
    assert dumped["degraded_reasons"] == []
    assert "last_heartbeat_at" in dumped
    assert "runtime_info" in dumped


async def test_uptime_seconds_is_int_and_monotonic(worker_state: WorkerState) -> None:
    """``uptime_seconds`` 必须为 ``int``（Patch-S6）且随 monotonic 递增。"""
    result = await handle_health_check({}, worker_state)
    assert isinstance(result.uptime_seconds, int)
    assert result.uptime_seconds >= 0

    # 人为把 monotonic_start 往前拨 2 秒
    worker_state.monotonic_start = time.monotonic() - 2
    result = await handle_health_check({}, worker_state)
    assert result.uptime_seconds >= 2


async def test_runtime_info_contains_platform_details(
    worker_state: WorkerState,
) -> None:
    """``runtime_info`` 包含 ``python_version`` / ``sqlite_version`` / ``platform``。"""
    result = await handle_health_check({}, worker_state)
    info = result.runtime_info

    assert info["python_version"] == sys.version.split()[0]
    assert isinstance(info["sqlite_version"], str)
    assert info["sqlite_version"]
    assert info["platform"] == sys.platform


async def test_degraded_when_reasons_present(worker_state: WorkerState) -> None:
    """``degraded_reasons`` 非空 → ``status=degraded``；为空 → ``ok``。"""
    worker_state.degraded_reasons.append("sidecar_restart_pending")
    result = await handle_health_check({}, worker_state)

    assert result.status == "degraded"
    assert result.degraded_reasons == ["sidecar_restart_pending"]
