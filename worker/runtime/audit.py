"""Provider 调用审计 + 费用粗估（Tranche 2 费用透明，PRD-ANA-006）。

两个入口：

- :func:`build_invocation`：从 provider 对象与实际字符量粗估一次调用的
  ``{provider, model, estimated_cost}``（``estimated_cost_per_1k`` 缺失时
  ``estimated_cost=None``）。
- :func:`record_provider_invocation`：向 ``audit_events``（0002 + 0005 补列）
  写一行 ``event_type='provider_invocation'`` 审计记录。payload 仅含
  ``{command, provider, model, estimated_cost}``——**绝不**携带密钥 /
  base_url / 提示词正文。

审计写入失败不允许击垮业务 handler：:func:`record_provider_invocation`
内部兜底吞掉异常并降级为日志。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from worker.runtime.models import CommandEnvelope

logger = logging.getLogger("worker.runtime.audit")


def build_invocation(
    provider: Any,
    chars: int,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    """构造 ``detail.invocation``：``{provider, model, estimated_cost}``。

    Args:
        provider: Provider 实例（duck-typed，读 ``name`` / ``model`` /
            ``estimated_cost_per_1k`` 属性）。
        chars: 实际字符量（prompt + 产出，或转写文本长度）。
        model: 显式模型名覆盖（如 renderer 用 template 名）。

    Returns:
        ``{"provider": str, "model": str, "estimated_cost": float | None}``；
        provider 无 ``estimated_cost_per_1k`` 配置时 ``estimated_cost=None``。
    """
    cost_per_1k = getattr(provider, "estimated_cost_per_1k", None)
    estimated: float | None = None
    if isinstance(cost_per_1k, (int, float)) and not isinstance(cost_per_1k, bool):
        estimated = round(max(chars, 0) / 1000.0 * float(cost_per_1k), 6)
    return {
        "provider": str(getattr(provider, "name", "unknown")),
        "model": str(model or getattr(provider, "model", None) or "unknown"),
        "estimated_cost": estimated,
    }


def record_provider_invocation(
    conn: Any,
    env: CommandEnvelope,
    invocation: dict[str, Any],
) -> None:
    """向 ``audit_events`` 写一行 ``provider_invocation`` 审计记录。

    payload 仅含 ``{command, provider, model, estimated_cost}``（无密钥）。
    任何写入失败都被吞掉并降级为日志，绝不让业务 handler 失败。
    """
    try:
        actor = env.actor if isinstance(env.actor, dict) else {}
        payload = {
            "command": env.commandType,
            "provider": invocation.get("provider"),
            "model": invocation.get("model"),
            "estimated_cost": invocation.get("estimated_cost"),
        }
        conn.execute(
            "INSERT INTO audit_events "
            "(id, actor, source_protocol, command, target, requested_scope, "
            "approval, result, correlation_id, timestamp, event_type, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"audit_{uuid.uuid4().hex}",
                f"{actor.get('type', 'unknown')}:{actor.get('id', 'unknown')}",
                env.source,
                env.commandType,
                env.projectId,
                None,
                None,
                "ok",
                env.commandId,
                datetime.now(UTC).isoformat(),
                "provider_invocation",
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - 审计失败绝不影响业务流
        logger.warning("provider_invocation audit write failed", exc_info=True)
