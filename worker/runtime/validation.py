"""payload 字段校验助手 —— handler 通用的"客户端错误"翻译。

**为什么单独一份**

同一段"limit 必须是正整数 + 可选上限"此前在 5 个 handler 里各写一遍
（``approvals.py`` / ``maintenance.py`` / ``queries.py`` × 3），错误文案一字
不差；pydantic ``spec = X(**payload)`` 的 ``try/except Exception → DispatchError
INVALID_ARGUMENT`` 包装也是 4 份拷贝（config / generate_script /
generate_topic / render_source）。重复本身不是灾难，**漂移才是** —— 一旦某处
改了文案或加了上限，其它几处就会静默不一致（review 报告里
``ListApprovalRequests`` 就没有上限，同概念的 ``ListAuditEvents`` 却
设了 500，就是这种漂移的实证）。

统一从这里走，一处改处处生效。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from worker.runtime.errors import DispatchError

__all__ = ["parse_spec", "require_positive_int"]


def parse_spec[ModelT: BaseModel](
    model: type[ModelT], payload: Any, *, what: str
) -> ModelT:
    """用 pydantic 模型校验 payload，失败转成干净的 ``DispatchError``。

    Args:
        model: ``BaseModel`` 子类，字段即 payload 的形状。
        payload: 原始 dict（``env.payload`` 或其中一段）。
        what: 面向用户的名词（``"script spec"`` / ``"config"`` …），
            直接落进错误消息，别用类名 —— 用户不认识 ``ScriptSpec`` 是什么。

    ``spec = model(**payload)`` 的构造异常（``ValidationError``、
    ``TypeError``、字段是非法类型时 pydantic 抛的东西）一律转成
    ``DispatchError("INVALID_ARGUMENT", ...)``；不让它冒到 bus 兜底
    路径变成 500。
    """
    try:
        return model(**(payload or {}))
    except Exception as e:  # noqa: BLE001 - 所有构造异常都是客户端错误
        raise DispatchError(
            "INVALID_ARGUMENT", f"bad {what}: {e}"
        ) from None


def require_positive_int(
    value: Any,
    *,
    name: str = "limit",
    maximum: int | None = None,
    default: int | None = None,
) -> int:
    """把 payload 里的 ``limit`` 之类字段收紧成**正整数**（可选上限）。

    - ``None`` → ``default``（若给了），否则 ``DispatchError``；
    - 显式传 ``bool`` 也算非法（``bool`` 是 ``int`` 的子类，不挡会 ``True`` → 1
      这种悄悄通过）；
    - ``maximum`` 提供时把超出的值 clamp 下来（``ListAuditEvents`` 500 就是这
      类，UI 传 10000 时不该把整张表拖进内存）；不超原样返回。
    """
    if value is None:
        if default is None:
            raise DispatchError(
                "INVALID_ARGUMENT", f"{name} must be a positive integer, got None"
            )
        value = default
    if not isinstance(value, int) or isinstance(value, bool):
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"{name} must be a positive integer, got {value!r}",
        )
    if value <= 0:
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"{name} must be a positive integer, got {value!r}",
        )
    if maximum is not None:
        return min(value, maximum)
    return value
