"""pytest 公共 fixtures。

通过 ``pyproject.toml`` 的 ``[tool.pytest.ini_options] asyncio_mode = "auto"``
启用自动异步模式；本模块不再重复声明。

提供：

- ``stream_pair``：基于内存 duplex 的 ``(reader, writer, peer_reader, peer_writer)``，
  通过 ``asyncio.start_server`` + 本地 TCP 环回构造，跨平台稳定。
- ``worker_state``：默认 :class:`WorkerState` 实例（含 ``shutdown_event``）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from worker.runtime.state import WorkerState


@dataclass
class StreamPair:
    """一对互联的 asyncio stream 端点。

    Attributes:
        reader: 本端读取。
        writer: 本端写入（对端可读）。
        peer_reader: 对端读取（验证 writer 写入内容）。
        peer_writer: 对端写入（供 reader 消费）。
        server: 底层 TCP server（清理用）。
    """

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    peer_reader: asyncio.StreamReader
    peer_writer: asyncio.StreamWriter
    server: asyncio.AbstractServer


@pytest.fixture
async def stream_pair() -> AsyncIterator[StreamPair]:
    """构造一对内存互联的 asyncio stream。

    Yields:
        :class:`StreamPair`；测试结束后自动关闭。
    """
    server_side: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []
    accepted = asyncio.Event()

    async def _on_client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        server_side.append((reader, writer))
        accepted.set()

    server = await asyncio.start_server(_on_client, host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]  # type: ignore[index]

    client_reader, client_writer = await asyncio.open_connection("127.0.0.1", port)
    await asyncio.wait_for(accepted.wait(), timeout=2.0)
    peer_reader, peer_writer = server_side[0]

    pair = StreamPair(
        reader=client_reader,
        writer=client_writer,
        peer_reader=peer_reader,
        peer_writer=peer_writer,
        server=server,
    )
    try:
        yield pair
    finally:
        for w in (pair.writer, pair.peer_writer):
            try:
                w.close()
                await w.wait_closed()
            except (ConnectionError, OSError, RuntimeError):
                pass
        pair.server.close()
        await pair.server.wait_closed()


@pytest.fixture
def worker_state() -> WorkerState:
    """提供默认 :class:`WorkerState`（含独立 ``shutdown_event``）。

    Returns:
        新的 ``WorkerState`` 实例（不写入环境变量）。
    """
    return WorkerState()
