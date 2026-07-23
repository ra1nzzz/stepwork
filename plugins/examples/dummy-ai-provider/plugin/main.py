"""Dummy AI Provider 示例插件入口（W8 L.33）。

W8 范围：本文件仅作为插件入口的**形状占位**，不被 worker 真正加载
（ADR-009 独立进程运行时属 V0.2）。它演示了一个 ai-provider 类型插件
的 entry.module 指向的模块应当导出的最小形状。

V0.2 启用独立进程后，worker 会以子进程拉起本模块，经 JSON-RPC 调用
``complete``。届时补齐真实实现即可。
"""

from __future__ import annotations

from typing import Any


def manifest() -> dict[str, Any]:
    """返回插件自描述（与 manifest.json 一致，供运行时校验）。"""
    return {
        "id": "dummy-ai-provider",
        "name": "Dummy AI Provider (示例)",
        "version": "0.1.0",
        "apiVersion": "1",
        "type": "ai-provider",
    }


async def complete(prompt: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """占位实现：回显 prompt 前 64 字符，证明插件通路可达。

    V0.2 起会被 worker 经 JSON-RPC 调用。W8 不触发此路径。
    """
    return {
        "ok": True,
        "echo": prompt[:64],
        "note": "dummy-ai-provider is a W8 example; real invocation requires V0.2 plugin runtime.",
    }
