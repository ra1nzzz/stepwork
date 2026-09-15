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
- **执行前先 :func:`reserve` 占位在跑席位**（review 抓到的 P1 并发漏洞）：
  此前 ``lookup → 执行 → remember`` 三步非原子，两条同 key 命令并发跑，
  都 miss 都执行一次，"重复计费"照样发生。现在第二条撞 :func:`reserve`
  失败即 ``DispatchError("IDEMPOTENT_INFLIGHT", ...)`` 让调用方稍后重试；
  失败路径必须 :func:`release` 归还占位，否则同 key 被永久钉住。

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

#: 占位哨兵：:func:`reserve` 写这条 key 时先落一行 ``result_json`` 等于此串的
#: 占位，表示"这条 key 正在被别的调用执行"；:func:`lookup` 看到它不能当结果
#: 返回。哨兵字符串选的是不可能出现在真 CommandResult JSON 里的字面量。
_INFLIGHT_SENTINEL = "__stepwork_idempotency_inflight__"


def lookup(conn: Any, env: CommandEnvelope) -> dict[str, Any] | None:
    """查找此 key 之前是否**已成功**执行过；命中则返回缓存结果。

    命中判定必须排除 :data:`_INFLIGHT_SENTINEL` —— 那条只是别的调用方的
    占位，还没有结果可返回。真正的"这条 key 正在被抢跑"判负交给
    :func:`reserve` 的 ``rowcount == 0`` 分支。
    """
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
    if row["result_json"] == _INFLIGHT_SENTINEL:
        # 别的调用正在执行；这次不重放也不接管 —— ``reserve`` 会把调用方拒收
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


def reserve(conn: Any, env: CommandEnvelope) -> bool:
    """尝试为此 key 占位在跑席位。

    Returns:
        ``True`` 表示本次调用**抢到**这条 key（可继续执行）；``False`` 表示
        已有别的调用在跑，调用方应放弃并给客户端回 ``IDEMPOTENT_INFLIGHT``。

    两条同 key 命令并发跑时，第二条撞主键约束即 ``INSERT OR IGNORE`` 不
    落地、``rowcount == 0``。SQLite 的语句级原子性就把"判存 + 落地"合并
    到了一条 ``INSERT OR IGNORE`` 上 —— 不需要 advisory lock 或额外表。

    幂等表不可用（迁移未到 / 磁盘 IO 错）时返回 ``True`` 放行，与既有
    降级语义一致：不能因为幂等表挂了就全线阻断业务。
    """
    key = env.idempotencyKey
    if not key or conn is None:
        return True
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO command_idempotency "
            "(workspace_id, command_type, idempotency_key, result_json, "
            "command_id, created_at) VALUES (?,?,?,?,?,?)",
            (
                env.workspaceId,
                env.commandType,
                key,
                _INFLIGHT_SENTINEL,
                env.commandId,
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()
    except Exception:  # noqa: BLE001
        logger.exception("idempotency reserve failed key=%s", key)
        return True
    return int(cur.rowcount) > 0


def release(conn: Any, env: CommandEnvelope) -> None:
    """执行失败时归还占位，让同 key 后续能重试。

    **只删哨兵行**：如果已经被 :func:`remember` 覆盖成真实结果（并发路径下
    理论不会走到这里 —— 只有抢到席位的一方才会执行、也只有它才会 release），
    就不要动那一行成功缓存。
    """
    key = env.idempotencyKey
    if not key or conn is None:
        return
    try:
        conn.execute(
            "DELETE FROM command_idempotency "
            "WHERE workspace_id=? AND command_type=? AND idempotency_key=? "
            "AND result_json=?",
            (env.workspaceId, env.commandType, key, _INFLIGHT_SENTINEL),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - 释放失败最坏是"这条 key 短期不能再试"
        logger.exception("idempotency release failed key=%s", key)


def remember(conn: Any, env: CommandEnvelope, result: dict[str, Any]) -> None:
    """缓存一次**成功**执行的结果（失败不缓存，见模块 docstring）。

    覆盖 :func:`reserve` 落的 INFLIGHT 占位行；主键一致，``INSERT OR REPLACE``
    把哨兵换成真结果。若 :func:`reserve` 之前未成功（即这条 key 是别的调用抢
    到的），调用方在 :func:`reserve` 阶段就被拒收、不会走到这里。
    """
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
