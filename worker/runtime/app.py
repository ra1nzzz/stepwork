"""应用入口 / 进程内命令运行器（W7 Phase 3）。

为 Rust 之外的调用方（CLI、MCP、测试）提供一层轻量、低依赖的门面，
使其可经 ``asyncio.run(run_command(...))`` 在**进程内**执行命令，而无需
启动 worker 子进程。

调用模式对齐 ``worker/dev_bridge.py`` 与
``worker.runtime.handlers.commands.handle_command``：构造 deps → 校验信封 →
dispatch。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from worker.runtime import ingest
from worker.runtime.bootstrap import bootstrap_db
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.providers.resolve import (
    resolve_ai,
    resolve_asr,
    resolve_renderer,
    resolve_tts,
)
from worker.runtime.state import WorkerState


async def run_command(
    raw: dict[str, Any], *, db_path: str | None = None
) -> dict[str, Any]:
    """执行一条命令，返回 ``CommandResult`` 的 dict 形态。

    Args:
        raw: 原始命令信封 dict（与 ``schemas/command-envelope.schema.json`` 一致）。
        db_path: 显式数据库路径；``None`` 时走默认路径
            （``$STEPWORK_HOME/stepwork.db``）。

    Returns:
        始终返回 dict（``CommandResult.model_dump()``）。任何异常都被包裹为
        ``{"ok": False, "error": str(e)}``，调用方永不崩溃。
    """
    try:
        ws = raw.get("workspaceId") or "ws-local"
        state = WorkerState()
        bootstrap_db(state, db_path=db_path)
        deps = Deps(
            repos=Repos(state.db_conn),
            ingest=ingest,
            asr=resolve_asr(ws),
            ai=resolve_ai(ws),
            tts=resolve_tts(ws),
            renderer=resolve_renderer(),
        )
        # ``dispatch`` 内部会 ``parse_envelope(raw)`` 校验信封并路由到
        # handler，最终对 ``CommandResult`` 调 ``model_dump()`` 返回 dict。
        return await dispatch(raw, deps)
    except Exception as e:  # noqa: BLE001 - 调用方需要干净的失败而非崩溃
        return {"ok": False, "error": str(e)}


def build_envelope(
    *,
    command_type: str,
    source: str,
    actor_type: str,
    workspace_id: str = "ws-local",
    project_id: str | None = None,
    idempotency_key: str | None = None,
    payload: dict[str, Any] | None = None,
    command_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """构造一个符合 ``command-envelope.schema.json`` 的信封 dict。

    复刻 ``apps/desktop/src/lib/tauri.ts`` 的 ``buildEnvelope`` 形状：
    ``commandId`` / ``commandType`` / ``schemaVersion="1"`` /
    ``actor={type,id}`` / ``source`` / ``workspaceId`` / ``projectId`` /
    ``idempotencyKey`` / ``payload`` / ``requestedAt``。

    ``request_id`` 为调用方透传占位，当前信封契约未定义该字段，故不写入 dict。

    Returns:
        信封 dict。``commandId`` / ``requestedAt`` 缺省时自动生成。
    """
    return {
        "commandId": command_id or str(uuid.uuid4()),
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": actor_type, "id": f"{actor_type}-cli"},
        "source": source,
        "workspaceId": workspace_id,
        "projectId": project_id,
        "idempotencyKey": idempotency_key,
        "payload": payload or {},
        "requestedAt": datetime.now(UTC).isoformat(),
    }
