"""``job.*`` / ``command.*`` RPC 入口（W3-W4 Batch 0）。

早期（W1）为占位；现在委托给 :mod:`worker.runtime.commands.bus` 进行
信封校验 + 路由。返回结构为 ``{"result": <CommandResult dict>}``
或 ``{"error": {"code": int, "message": str}}``，由 :mod:`worker.runtime.__main__`
的 ``_dispatch`` 转为 JSON-RPC 响应帧。

deps 装配统一走 :func:`worker.runtime.deps.build_deps` —— 与进程内门面
``worker.runtime.app.run_command`` 共用同一份 Provider 缓存 + notify +
worker_state，不再有"两条装配长得不一样"的漂移。
"""

from __future__ import annotations

from typing import Any

from worker.runtime.commands.bus import dispatch
from worker.runtime.deps import build_deps
from worker.runtime.state import WorkerState


async def handle_command(
    params: dict[str, Any] | None,
    state: WorkerState,
) -> dict[str, Any]:
    """处理 ``job.*`` / ``command.*`` 请求。

    Args:
        params: JSON-RPC 参数（应含 ``envelope`` 键）。
        state: Worker 运行期状态（含 ``db_conn``）。

    Returns:
        ``{"result": ...}`` 或 ``{"error": {...}}``。
    """
    if state.db_conn is None:
        return {"error": {"code": -32000, "message": "worker db not initialized"}}

    raw = (params or {}).get("envelope")
    if raw is None:
        return {"error": {"code": -32602, "message": "missing envelope in params"}}

    ws_id = (raw or {}).get("workspaceId")
    deps = await build_deps(state, ws_id)
    # 包装为 {"result": <CommandResult dict>}，与模块 docstring 合约一致；
    # _dispatch 据此走 result 分支（否则会把 error 字符串当 dict 调 .get 崩）。
    return {"result": await dispatch(raw, deps)}
