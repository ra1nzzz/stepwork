"""``job.*`` / ``command.*`` RPC 入口（W3-W4 Batch 0）。

早期（W1）为占位；现在委托给 :mod:`worker.runtime.commands.bus` 进行
信封校验 + 路由。返回结构为 ``{"result": <CommandResult dict>}``
或 ``{"error": {"code": int, "message": str}}``，由 :mod:`worker.runtime.__main__`
的 ``_dispatch`` 转为 JSON-RPC 响应帧。
"""

from __future__ import annotations

from typing import Any

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.providers.resolve import resolve_ai, resolve_asr
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

    repos = Repos(state.db_conn)
    deps = Deps(
        repos=repos,
        ingest=ingest,
        asr=resolve_asr(),
        ai=resolve_ai(),
    )
    return dispatch(raw, deps)
