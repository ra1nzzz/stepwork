"""命令信封校验（W3-W4 Batch 0）。"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from worker.runtime.models import CommandEnvelope


class EnvelopeError(ValueError):
    """信封结构非法。"""


def parse_envelope(raw: dict[str, Any]) -> CommandEnvelope:
    """把原始 dict 校验为 :class:`CommandEnvelope`。

    Raises:
        EnvelopeError: 字段缺失或类型不符。
    """
    try:
        return CommandEnvelope.model_validate(raw)
    except ValidationError as e:
        raise EnvelopeError(f"invalid command envelope: {e}") from e
