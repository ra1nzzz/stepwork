"""``worker.runtime.rpc`` 单元测试（6 用例）。

覆盖：

1. request 帧 round-trip
2. notification 帧 round-trip
3. 超大帧 → :class:`FrameTooLargeError` 且载荷被丢弃
4. 非法 JSON → :class:`ParseError`
5. 长度前缀与字节流不匹配 → :class:`ConnectionClosedError`
6. :data:`MAX_FRAME_SIZE` 常量校验
"""

from __future__ import annotations

import struct

import pytest

from worker.runtime.rpc import (
    MAX_FRAME_SIZE,
    ConnectionClosedError,
    FrameTooLargeError,
    ParseError,
    RpcFrame,
    make_notification,
    read_frame,
    write_frame,
)
from worker.tests.conftest import StreamPair


async def test_round_trip_request(stream_pair: StreamPair) -> None:
    """request 帧写后可被对端完整读回。"""
    original = RpcFrame(id="req-1", method="runtime.health_check", params={})
    await write_frame(stream_pair.writer, original)

    received = await read_frame(stream_pair.peer_reader)
    assert received.jsonrpc == "2.0"
    assert received.id == "req-1"
    assert received.method == "runtime.health_check"
    assert received.params == {}
    assert received.error is None


async def test_round_trip_notification(stream_pair: StreamPair) -> None:
    """notification（无 id）写后读回保持 ``id=None``。"""
    original = make_notification("runtime.heartbeat", {"alive": True})
    await write_frame(stream_pair.writer, original)

    received = await read_frame(stream_pair.peer_reader)
    assert received.id is None
    assert received.method == "runtime.heartbeat"
    assert received.params == {"alive": True}


async def test_oversized_frame_raises_and_discards(stream_pair: StreamPair) -> None:
    """超大帧：抛 :class:`FrameTooLargeError`，载荷被丢弃，后续帧仍可正常读取。"""
    oversize = MAX_FRAME_SIZE + 1
    payload = b"x" * oversize
    stream_pair.peer_writer.write(struct.pack(">I", oversize) + payload)

    # 紧跟一个合法小帧，验证丢弃后连接可用
    follow_up = RpcFrame(id="after", method="runtime.heartbeat", params={})
    follow_bytes = follow_up.model_dump_json(exclude_none=True).encode("utf-8")
    stream_pair.peer_writer.write(struct.pack(">I", len(follow_bytes)) + follow_bytes)
    await stream_pair.peer_writer.drain()

    with pytest.raises(FrameTooLargeError) as exc_info:
        await read_frame(stream_pair.reader)
    assert exc_info.value.size == oversize

    recovered = await read_frame(stream_pair.reader)
    assert recovered.id == "after"
    assert recovered.method == "runtime.heartbeat"


async def test_malformed_json_raises_parse_error(stream_pair: StreamPair) -> None:
    """非法 JSON 文本 → :class:`ParseError`。"""
    bad = b"{not-a-json"
    stream_pair.peer_writer.write(struct.pack(">I", len(bad)) + bad)
    await stream_pair.peer_writer.drain()

    with pytest.raises(ParseError):
        await read_frame(stream_pair.reader)


async def test_truncated_payload_raises_connection_closed(
    stream_pair: StreamPair,
) -> None:
    """长度前缀声明 N 字节但字节流提前结束 → :class:`ConnectionClosedError`。"""
    stream_pair.peer_writer.write(struct.pack(">I", 100) + b"short")
    await stream_pair.peer_writer.drain()
    stream_pair.peer_writer.close()

    with pytest.raises(ConnectionClosedError):
        await read_frame(stream_pair.reader)


def test_max_frame_size_constant() -> None:
    """:data:`MAX_FRAME_SIZE` 等于 1 MiB。"""
    assert MAX_FRAME_SIZE == 1024 * 1024
