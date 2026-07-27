"""行转换与时间戳的公共工具。

``_row_to_dict`` 此前在 **10 个** handler 里各写一份、``_now()`` 在 **8 个**
里各写一份。逐份看都只有三行，但每多一份就多一处可能悄悄写岔的地方
（比如某处忘了 ``str()`` 转型、某处用了本地时间而不是 UTC）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def row_to_dict(row: Any) -> dict[str, Any]:
    """``sqlite3.Row`` → 普通 dict（列名 → 值，原始类型保留）。

    TEXT 列 sqlite3 已返回 str，INTEGER/REAL 保留数值类型，NULL 保留 None。
    """
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: Any) -> list[dict[str, Any]]:
    return [row_to_dict(r) for r in rows]


def now_iso() -> str:
    """当前 UTC 时间的 ISO8601 字符串。

    统一走 UTC：库里混入本地时间会让跨时区的排序和比较悄悄出错，而且这类
    错误在单一时区开发时根本看不出来。
    """
    return datetime.now(UTC).isoformat()
