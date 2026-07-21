"""``job.*`` / Command Bus 占位 handler（W1）。

W2 与 Command Bus 一并实现；W1 仅返回 ``-32601 Method not implemented``。
"""

from __future__ import annotations

from typing import Any

from worker.runtime.state import WorkerState


async def handle_command(
    params: dict[str, Any] | None,
    state: WorkerState,
) -> dict[str, Any]:
    """``job.*`` 占位实现。

    Args:
        params: JSON-RPC 参数。
        state: Worker 运行期状态。

    Returns:
        JSON-RPC 错误对象字典，``code=-32601``。
    """
    del params, state
    return {
        "error": {
            "code": -32601,
            "message": "Method not implemented in W1",
        }
    }
