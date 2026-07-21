"""周期性 ``runtime.heartbeat`` 发送循环。

按 SYSTEM_SPEC §6.3：Sidecar 必须每 5 秒发送一次心跳。
"""

from __future__ import annotations

import asyncio
import contextlib

from worker.runtime.handlers import lifecycle
from worker.runtime.rpc import make_notification, write_frame
from worker.runtime.state import WorkerState

DEFAULT_HEARTBEAT_INTERVAL: float = 5.0
"""默认心跳间隔（秒）。"""


async def heartbeat_loop(
    writer: asyncio.StreamWriter,
    state: WorkerState,
    shutdown_event: asyncio.Event,
    interval: float = DEFAULT_HEARTBEAT_INTERVAL,
) -> None:
    """周期性发送 ``runtime.heartbeat`` notification，直至 ``shutdown_event`` 触发。

    实现方式：每轮先发送一次心跳，然后 ``asyncio.wait_for(shutdown_event.wait(),
    timeout=interval)``；``TimeoutError`` 视为正常，继续下一轮。

    Args:
        writer: 输出流（通常指向 stdout）。
        state: Worker 运行期状态（用于更新 ``last_heartbeat_at``）。
        shutdown_event: 由 ``__main__`` 控制的退出信号。
        interval: 心跳间隔（秒）。
    """
    while not shutdown_event.is_set():
        params = await lifecycle.handle_heartbeat(state)
        frame = make_notification("runtime.heartbeat", params)
        with contextlib.suppress(ConnectionError, OSError):
            # 对端已关闭时静默退出循环；主循环会感知并清理
            await write_frame(writer, frame)

        with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
