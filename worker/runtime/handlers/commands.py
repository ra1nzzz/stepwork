"""``job.*`` / ``command.*`` RPC 入口（W3-W4 Batch 0）。

早期（W1）为占位；现在委托给 :mod:`worker.runtime.commands.bus` 进行
信封校验 + 路由。返回结构为 ``{"result": <CommandResult dict>}``
或 ``{"error": {"code": int, "message": str}}``，由 :mod:`worker.runtime.__main__`
的 ``_dispatch`` 转为 JSON-RPC 响应帧。
"""

from __future__ import annotations

import asyncio
from typing import Any

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.providers.resolve import get_provider_bundle
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
    repos = Repos(state.db_conn)
    # Provider 六件套走进程级缓存（``get_provider_bundle``）—— 此前每条命令
    # 都重建，``WhisperASRProvider._model`` 只在实例内部缓存，实例一换就要
    # 重新 ``WhisperModel(...)`` 加载几百 MB~GB 的模型；``shutil.which`` × 2
    # + ``find_spec`` 也在扫盘（连 GetConfig 都付全价）。
    # 缓存命中只是 dict 拷贝；miss 会跑上面那些**阻塞 IO**，故整体丢
    # ``asyncio.to_thread``——首次构建（往往是冷启动第一条 TranscribeSource）
    # 不占事件循环，心跳/取消照跑。
    bundle = await asyncio.to_thread(get_provider_bundle, ws_id)
    deps = Deps(
        repos=repos,
        ingest=ingest,
        asr=bundle["asr"],
        ai=bundle["ai"],
        tts=bundle["tts"],
        image=bundle["image"],
        renderer=bundle["renderer"],
        scene_detector=bundle["scene_detector"],
        notify=state.notify,
        worker_state=state,
    )
    # 包装为 {"result": <CommandResult dict>}，与模块 docstring 合约一致；
    # _dispatch 据此走 result 分支（否则会把 error 字符串当 dict 调 .get 崩）。
    return {"result": await dispatch(raw, deps)}
