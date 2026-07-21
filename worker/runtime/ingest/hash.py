"""文件哈希原语（W3，Batch 0 先落地）。

sha256 防碰撞（三角色头脑风暴 P0）；大文件分块读取，避免一次性占内存。
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def hash_file(path: str | Path) -> str:
    """计算文件 sha256 hex（分块）。

    Args:
        path: 文件路径（``str`` / ``Path`` / 任何 ``__fspath__`` 对象）。

    Returns:
        64 字符 hex 串。
    """
    h = hashlib.sha256()
    with open(Path(path), "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_bytes(data: bytes) -> str:
    """计算字节序列 sha256 hex。"""
    return hashlib.sha256(data).hexdigest()
