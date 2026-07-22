"""命令信封校验（W3-W4 Batch 0）。

``parse_envelope`` 先经 pydantic 做结构校验，再补一层**契约校验**
（actor.type 取值 + schemaVersion 一致性），确保运行期与
``schemas/command-envelope.schema.json`` 单一事实源保持一致。契约漂移
（如未知 actor.type 或版本错配）会被转译为干净的 :class:`EnvelopeError`
（由 Command Bus 转为 ``CommandResult(ok=False)``），而非静默放行或崩溃。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from worker.runtime.models import CommandEnvelope

# 与 schemas/command-envelope.schema.json 的 actor.type 枚举保持一致
ALLOWED_ACTOR_TYPES: tuple[str, ...] = ("user", "agent", "plugin", "system", "desktop")
# 当前信封契约版本（schema.json 中 schemaVersion 为 const "1"）
EXPECTED_SCHEMA_VERSION = "1"


class EnvelopeError(ValueError):
    """信封结构或契约非法。"""


def parse_envelope(raw: dict[str, Any]) -> CommandEnvelope:
    """把原始 dict 校验为 :class:`CommandEnvelope`。

    Raises:
        EnvelopeError: 字段缺失 / 类型不符 / 契约漂移（actor.type 或
            schemaVersion 不合法）。
    """
    try:
        env = CommandEnvelope.model_validate(raw)
    except ValidationError as e:
        raise EnvelopeError(f"invalid command envelope: {e}") from e

    # 契约校验：actor.type 取值
    actor = env.actor if isinstance(env.actor, dict) else {}
    actor_type = actor.get("type")
    if actor_type not in ALLOWED_ACTOR_TYPES:
        raise EnvelopeError(
            f"invalid actor.type: {actor_type!r} "
            f"(expected one of {ALLOWED_ACTOR_TYPES})"
        )

    # 契约校验：schemaVersion 一致性
    if env.schemaVersion != EXPECTED_SCHEMA_VERSION:
        raise EnvelopeError(
            f"unsupported schemaVersion: {env.schemaVersion!r} "
            f"(expected {EXPECTED_SCHEMA_VERSION!r})"
        )

    return env
