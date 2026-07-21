"""JSON-RPC 2.0 over stdio（长度前缀帧）。

帧格式::

    [4 字节大端 uint32 长度][JSON UTF-8 字节流]

W1.3 实现要点：

- 最大帧 1 MiB（`MAX_FRAME_SIZE`）
- 长度前缀 >MAX_FRAME_SIZE：读 N 字节丢弃 → 抛 :class:`FrameTooLargeError`
- 长度与实际字节流不匹配：视为对端崩溃 → 抛 :class:`ConnectionClosedError`
- JSON 解析失败 / 非 UTF-8：抛 :class:`ParseError`
"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

MAX_FRAME_SIZE: int = 1 * 1024 * 1024
"""单帧最大字节数（1 MiB）。"""

_HEADER_SIZE: int = 4
"""长度前缀字节数（大端 uint32）。"""


class RpcError(BaseModel):
    """JSON-RPC 2.0 Error Object。"""

    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: dict[str, Any] | None = None


class RpcFrame(BaseModel):
    """JSON-RPC 2.0 消息（request / response / notification 通用）。"""

    model_config = ConfigDict(extra="forbid")

    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    method: str | None = None
    params: dict[str, Any] | None = None
    result: Any | None = None
    error: RpcError | None = None


class RpcProtocolError(Exception):
    """RPC 协议层异常基类。"""


class ParseError(RpcProtocolError):
    """JSON 解析失败 / 非 UTF-8 / schema 校验失败。"""


class FrameTooLargeError(RpcProtocolError):
    """单帧长度超过 :data:`MAX_FRAME_SIZE`。

    抛出前已从 reader 中读取并丢弃完整载荷，连接可继续使用。
    """

    def __init__(self, size: int) -> None:
        """初始化。

        Args:
            size: 对端声明的帧长度（字节）。
        """
        super().__init__(f"frame size {size} exceeds MAX_FRAME_SIZE {MAX_FRAME_SIZE}")
        self.size = size


class ConnectionClosedError(RpcProtocolError):
    """对端崩溃或长度前缀与实际字节流不匹配。"""


async def _read_exactly(reader: asyncio.StreamReader, n: int) -> bytes:
    """读取恰好 ``n`` 字节，不足时抛 :class:`ConnectionClosedError`。

    Args:
        reader: asyncio 流读取器。
        n: 期望读取的字节数。

    Returns:
        读取到的字节序列（长度严格等于 ``n``）。

    Raises:
        ConnectionClosedError: 流提前结束或长度不匹配。
    """
    try:
        data = await reader.readexactly(n)
    except asyncio.IncompleteReadError as exc:
        raise ConnectionClosedError(
            f"stream closed: expected {n} bytes, got {len(exc.partial)}"
        ) from exc
    except (ConnectionResetError, BrokenPipeError) as exc:
        raise ConnectionClosedError(f"connection reset: {exc}") from exc
    if len(data) != n:
        raise ConnectionClosedError(f"length mismatch: expected {n}, got {len(data)}")
    return data


async def _discard(reader: asyncio.StreamReader, n: int) -> None:
    """从 reader 中读取并丢弃 ``n`` 字节（分块避免一次性大内存）。

    Args:
        reader: asyncio 流读取器。
        n: 待丢弃字节数。

    Raises:
        ConnectionClosedError: 流提前结束。
    """
    remaining = n
    chunk = 64 * 1024
    while remaining > 0:
        step = min(chunk, remaining)
        await _read_exactly(reader, step)
        remaining -= step


async def read_frame(reader: asyncio.StreamReader) -> RpcFrame:
    """从 reader 读取一帧并解析为 :class:`RpcFrame`。

    Args:
        reader: asyncio 流读取器。

    Returns:
        解析后的 :class:`RpcFrame`。

    Raises:
        FrameTooLargeError: 长度前缀 > :data:`MAX_FRAME_SIZE`（载荷已丢弃）。
        ParseError: JSON / UTF-8 / schema 校验失败。
        ConnectionClosedError: 对端崩溃或长度不匹配。
    """
    header = await _read_exactly(reader, _HEADER_SIZE)
    (length,) = struct.unpack(">I", header)

    if length > MAX_FRAME_SIZE:
        await _discard(reader, length)
        raise FrameTooLargeError(length)

    payload = await _read_exactly(reader, length)

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError(f"payload is not valid UTF-8: {exc}") from exc

    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid JSON: {exc}") from exc

    try:
        return RpcFrame.model_validate(raw)
    except ValidationError as exc:
        raise ParseError(f"rpc frame schema validation failed: {exc}") from exc


async def write_frame(writer: asyncio.StreamWriter, frame: RpcFrame) -> None:
    """将 :class:`RpcFrame` 序列化并写入 writer。

    序列化规则：
    - request / notification：剔除 ``None`` 字段，保持报文紧凑。
    - response（``result`` 或 ``error`` 非空）：保留 ``id`` 字段，即使为 ``None``
      （JSON-RPC 2.0 §5 要求 parse error 等场景必须回 ``"id": null``）。

    Args:
        writer: asyncio 流写入器。
        frame: 待发送的 RPC 帧。

    Raises:
        ConnectionClosedError: 底层连接已关闭。
    """
    is_response = frame.result is not None or frame.error is not None
    if is_response:
        # 保留 id（即使 None），但剔除未使用的 method/params 与另一结果槽位
        exclude: set[str] = {"method", "params"}
        if frame.error is not None:
            exclude.add("result")
        else:
            exclude.add("error")
        body = frame.model_dump_json(exclude=exclude).encode("utf-8")
    else:
        body = frame.model_dump_json(exclude_none=True).encode("utf-8")
    header = struct.pack(">I", len(body))
    try:
        writer.write(header + body)
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError) as exc:
        raise ConnectionClosedError(f"write failed: {exc}") from exc


def make_error_response(
    request_id: str | int | None,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> RpcFrame:
    """构造 JSON-RPC 错误响应帧。

    Args:
        request_id: 对应请求的 id；解析失败场景使用 ``None``。
        code: JSON-RPC 错误码。
        message: 错误消息。
        data: 可选附加数据。

    Returns:
        错误响应帧。
    """
    return RpcFrame(id=request_id, error=RpcError(code=code, message=message, data=data))


def make_result_response(request_id: str | int | None, result: Any) -> RpcFrame:
    """构造 JSON-RPC 成功响应帧。

    Args:
        request_id: 对应请求的 id。
        result: 结果对象（需可 JSON 序列化）。

    Returns:
        成功响应帧。
    """
    return RpcFrame(id=request_id, result=result)


def make_notification(method: str, params: dict[str, Any] | None = None) -> RpcFrame:
    """构造 JSON-RPC notification（无 id）。

    Args:
        method: 方法名。
        params: 可选参数。

    Returns:
        notification 帧。
    """
    return RpcFrame(method=method, params=params)
