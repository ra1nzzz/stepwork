"""Tranche 1（T1）：主循环并发 dispatch 测试。

守卫契约「长命令不得阻塞读循环」：``command.dispatch`` 类 request 帧改为
``asyncio.create_task`` 并发处理后，慢命令执行期间后续帧（如
``runtime.health_check``、``CancelJob``）必须能被读取并先行响应。

驱动方式：monkeypatch ``sys.stdin`` / ``sys.stdout`` 为 BytesIO 包装
（``amain`` 经 ``_open_stdin_stdout`` 的线程桥接读写），并把
``handlers.commands.handle_command`` 换成慢 fake（sleep 0.3s）。
输入帧序：慢 command.dispatch(id=1) → runtime.health_check(id=2) → EOF。
断言：id=2 的响应先于 id=1 写出（改造前主循环内联 await，顺序必然 1 → 2）。
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from worker.runtime.handlers import commands


def _frame_bytes(payload: dict[str, Any]) -> bytes:
    """把一条 JSON-RPC 消息编码为长度前缀帧。"""
    body = json.dumps(payload).encode("utf-8")
    return struct.pack(">I", len(body)) + body


def _parse_frames(data: bytes) -> list[dict[str, Any]]:
    """把 stdout 字节流解析回 JSON-RPC 消息列表（顺带校验帧完整性）。"""
    frames: list[dict[str, Any]] = []
    offset = 0
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        body = data[offset + 4 : offset + 4 + length]
        assert len(body) == length, "frame truncated / interleaved"
        frames.append(json.loads(body.decode("utf-8")))
        offset += 4 + length
    return frames


async def test_slow_command_does_not_block_read_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """慢命令（0.3s）执行期间，后续 health_check 必须先行得到响应。"""
    from worker.runtime.__main__ import amain

    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))

    stdin_bytes = _frame_bytes(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "command.dispatch",
            "params": {"envelope": {"whatever": True}},
        }
    ) + _frame_bytes(
        {"jsonrpc": "2.0", "id": 2, "method": "runtime.health_check"}
    )

    stdout_buf = io.BytesIO()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(stdin_bytes)))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=stdout_buf))

    async def slow_handle_command(
        params: dict[str, Any] | None, state: Any
    ) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return {"result": {"ok": True, "detail": {"slow": True}}}

    monkeypatch.setattr(commands, "handle_command", slow_handle_command)

    exit_code = await asyncio.wait_for(amain(), timeout=10.0)
    assert exit_code == 0

    frames = _parse_frames(stdout_buf.getvalue())
    # ready notification 必须最先出现
    assert frames[0].get("method") == "runtime.ready"

    response_ids = [f["id"] for f in frames if "id" in f and f.get("id") is not None]
    assert 1 in response_ids, f"slow command response missing: {frames}"
    assert 2 in response_ids, f"health_check response missing: {frames}"
    # 关键断言：health_check(id=2) 先于慢命令(id=1) 响应
    assert response_ids.index(2) < response_ids.index(1), (
        f"read loop was blocked by slow command; response order: {response_ids}"
    )
    # 慢命令响应内容完整（帧未被并发写破坏）
    slow_resp = next(f for f in frames if f.get("id") == 1)
    assert slow_resp["result"]["detail"]["slow"] is True
