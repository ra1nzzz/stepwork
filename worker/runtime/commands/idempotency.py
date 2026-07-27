"""命令幂等（PRD §13「重复任务幂等阻止重复输出」）。

``command-envelope.schema.json`` 早就有 ``idempotencyKey`` 并写着
「Side-effecting commands SHOULD provide one」，但此前全仓**从未消费**它：
重复提交同一条命令会重复产出内容版本、重复计费。

策略（刻意保守）：

- **只缓存成功结果**。失败不缓存 —— 否则一次网络抖动导致的失败会被永久
  钉死，用户拿同一个 key 重试永远拿到那次失败。
- 作用域 = ``(workspace_id, command_type, idempotency_key)``，不同命令
  即便复用同一个 key 也互不干扰。
- 重放时在 ``detail`` 打 ``idempotent_replay=true``，让调用方能区分
  「这次真跑了」与「这是上次的结果」。

缓存读写失败一律降级为「正常执行」，绝不因幂等表不可用而阻断业务。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from worker.runtime.models import CommandEnvelope

logger = logging.getLogger("worker.runtime.commands")

#: 重放标记键（写进 detail，供调用方区分真跑与重放）
REPLAY_FLAG = "idempotent_replay"


def lookup(conn: Any, env: CommandEnvelope) -> dict[str, Any] | None:
    """查找此 key 之前是否已成功执行过；命中则返回缓存结果。"""
    key = env.idempotencyKey
    if not key or conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT result_json FROM command_idempotency "
            "WHERE workspace_id=? AND command_type=? AND idempotency_key=?",
            (env.workspaceId, env.commandType, key),
        ).fetchone()
    except Exception:  # noqa: BLE001 - 幂等表不可用时退化为正常执行
        logger.exception("idempotency lookup failed key=%s", key)
        return None
    if row is None:
        return None
    try:
        cached: dict[str, Any] = json.loads(row["result_json"])
    except (TypeError, ValueError):
        return None
    # 标记为重放，并保留本次 commandId（调用方据此关联自己的请求）
    detail = cached.get("detail")
    cached["detail"] = {**(detail if isinstance(detail, dict) else {}), REPLAY_FLAG: True}
    cached["commandId"] = env.commandId
    return cached


def remember(conn: Any, env: CommandEnvelope, result: dict[str, Any]) -> None:
    """缓存一次**成功**执行的结果（失败不缓存，见模块 docstring）。"""
    key = env.idempotencyKey
    if not key or conn is None or not result.get("ok"):
        return
    try:
        conn.execute(
            "INSERT OR REPLACE INTO command_idempotency "
            "(workspace_id, command_type, idempotency_key, result_json, "
            "command_id, created_at) VALUES (?,?,?,?,?,?)",
            (
                env.workspaceId,
                env.commandType,
                key,
                json.dumps(result, ensure_ascii=False),
                env.commandId,
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - 缓存失败不影响本次结果
        logger.exception("idempotency remember failed key=%s", key)
