"""``python -m worker.runtime`` 入口。

启动流程：

1. 记录 ``monotonic_start``
2. 读取/生成 session token，构造 :class:`WorkerState`（含 ``shutdown_event``）
3. 打开 stdin/stdout 为 asyncio StreamReader/StreamWriter（基于 ``sys.stdin.buffer`` /
   ``sys.stdout.buffer``）
4. 计算 ``startup_duration_ms``，发送 ``runtime.ready`` notification
5. 启动 ``heartbeat_loop`` task
6. 主循环：``read_frame`` → 校验 ``params._session_token`` → 路由 → 写回 response
7. 错误处理：
   - :class:`ParseError` → 回 ``-32700``（id=null）→ break
   - :class:`FrameTooLargeError` → 回 ``-32600`` → continue
   - :class:`ConnectionClosedError` → break
8. 收到 ``runtime.shutdown`` → 设置 ``state.shutdown_event`` → 等 heartbeat 退出 →
   关闭 writer → 退出码 0
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import IO

from worker.runtime.handlers import commands, health, lifecycle
from worker.runtime.heartbeat import heartbeat_loop
from worker.runtime.rpc import (
    ConnectionClosedError,
    FrameTooLargeError,
    ParseError,
    RpcFrame,
    make_error_response,
    make_notification,
    make_result_response,
    read_frame,
    write_frame,
)
from worker.runtime.state import WorkerState

logger = logging.getLogger("worker.runtime")

JSONRPC_PARSE_ERROR: int = -32700
"""JSON-RPC 2.0：Parse error。"""

JSONRPC_INVALID_REQUEST: int = -32600
"""JSON-RPC 2.0：Invalid Request。"""

JSONRPC_METHOD_NOT_FOUND: int = -32601
"""JSON-RPC 2.0：Method not found。"""

JSONRPC_UNAUTHORIZED: int = -32001
"""自定义错误码：session token 校验失败。"""

_SESSION_TOKEN_KEY: str = "_session_token"
"""请求 params 中携带 session token 的保留字段名。"""


def _configure_logging() -> None:
    """将日志输出到 stderr（stdout 专用于 RPC 帧）。"""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","name":"%(name)s","msg":"%(message)s"}',
    )


def _stdin_binary() -> IO[bytes]:
    """返回 stdin 的二进制缓冲对象。

    Returns:
        ``sys.stdin.buffer``（存在时），否则回退到 ``sys.stdin`` 本身。
    """
    buffer = getattr(sys.stdin, "buffer", None)
    return buffer if buffer is not None else sys.stdin  # type: ignore[return-value]


def _stdout_binary() -> IO[bytes]:
    """返回 stdout 的二进制缓冲对象。

    Returns:
        ``sys.stdout.buffer``（存在时），否则回退到 ``sys.stdout`` 本身。
    """
    buffer = getattr(sys.stdout, "buffer", None)
    return buffer if buffer is not None else sys.stdout  # type: ignore[return-value]


async def _open_stdin_stdout() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """把进程 stdin/stdout 包装为 asyncio StreamReader/StreamWriter。

    通过 ``loop.connect_read_pipe`` / ``loop.connect_write_pipe`` 把
    ``sys.stdin.buffer`` / ``sys.stdout.buffer`` 接入事件循环。

    Returns:
        ``(reader, writer)`` 元组。
    """
    loop = asyncio.get_running_loop()

    reader = asyncio.StreamReader()
    reader_protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: reader_protocol, _stdin_binary())

    writer_transport, writer_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, _stdout_binary()
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, loop)
    return reader, writer


def _check_session_token(frame: RpcFrame, state: WorkerState) -> bool:
    """校验请求帧的 ``params._session_token`` 与 ``state.session_token`` 一致。

    notification（无 id）与无 params 的帧视为不携带 token，默认放行（W1 宽容策略，
    仅对显式提供 ``params`` 的 request 强制校验）。

    Args:
        frame: 入站 RPC 帧。
        state: Worker 运行期状态（含 ``session_token``）。

    Returns:
        校验通过返回 ``True``；显式提供了 params 但 token 缺失或不匹配返回 ``False``。
    """
    if frame.id is None or frame.params is None:
        return True
    token = frame.params.get(_SESSION_TOKEN_KEY)
    if token is None:
        # W1 宽容：允许不带 token 的本地调试请求
        return True
    return bool(isinstance(token, str) and token == state.session_token)


async def _dispatch(frame: RpcFrame, state: WorkerState) -> RpcFrame | None:
    """根据 ``frame.method`` 路由到具体 handler 并构造响应。

    Args:
        frame: 入站请求帧。
        state: Worker 运行期状态（含 ``shutdown_event``）。

    Returns:
        响应帧；对 notification（无 id）返回 ``None``。
    """
    method = frame.method or ""
    params = frame.params

    if method == "runtime.health_check":
        health_result = await health.handle_health_check(params, state)
        return make_result_response(frame.id, health_result.model_dump(mode="json"))

    if method == "runtime.shutdown":
        shutdown_result = await lifecycle.handle_shutdown(params, state, state.shutdown_event)
        return make_result_response(frame.id, shutdown_result)

    if method.startswith("job.") or method.startswith("command."):
        payload = await commands.handle_command(params, state)
        if "result" in payload:
            return make_result_response(frame.id, payload["result"])
        err = payload.get("error", {})
        return make_error_response(
            frame.id,
            code=int(err.get("code", JSONRPC_METHOD_NOT_FOUND)),
            message=str(err.get("message", "Method not implemented")),
        )

    return make_error_response(
        frame.id,
        code=JSONRPC_METHOD_NOT_FOUND,
        message=f"Method not found: {method}",
    )


async def amain() -> int:
    """异步主入口。

    Returns:
        进程退出码（0 表示正常）。
    """
    _configure_logging()

    monotonic_start = time.monotonic()
    state = WorkerState(monotonic_start=monotonic_start)

    # W3-W4 Batch 0：启动即初始化数据库层（Command Bus 依赖）
    from worker.runtime.bootstrap import bootstrap_db

    bootstrap_db(state)

    reader, writer = await _open_stdin_stdout()

    # 计算启动耗时（v1.1 Patch-U3），再发送 ready
    state.startup_duration_ms = int((time.monotonic() - monotonic_start) * 1000)

    ready_params = await lifecycle.handle_ready(state)
    await write_frame(writer, make_notification("runtime.ready", ready_params))
    logger.info(
        "worker ready pid=%s protocol=%s startup_ms=%s",
        state.pid,
        state.protocol_version,
        state.startup_duration_ms,
    )

    heartbeat_task = asyncio.create_task(
        heartbeat_loop(writer, state, state.shutdown_event),
        name="runtime-heartbeat",
    )

    exit_code = 0
    try:
        while not state.shutdown_event.is_set():
            try:
                frame = await read_frame(reader)
            except ParseError as exc:
                logger.warning("parse error: %s", exc)
                await write_frame(
                    writer,
                    make_error_response(None, JSONRPC_PARSE_ERROR, f"Parse error: {exc}"),
                )
                break
            except FrameTooLargeError as exc:
                logger.warning("frame too large: %s", exc)
                await write_frame(
                    writer,
                    make_error_response(
                        None,
                        JSONRPC_INVALID_REQUEST,
                        f"Frame exceeds {exc.size} bytes (max 1 MiB)",
                    ),
                )
                continue
            except ConnectionClosedError as exc:
                logger.info("connection closed: %s", exc)
                break

            if not _check_session_token(frame, state):
                logger.warning("session token mismatch id=%s", frame.id)
                await write_frame(
                    writer,
                    make_error_response(
                        frame.id,
                        JSONRPC_UNAUTHORIZED,
                        "Invalid session token",
                    ),
                )
                continue

            response = await _dispatch(frame, state)
            if response is not None:
                await write_frame(writer, response)
    finally:
        state.shutdown_event.set()
        heartbeat_task.cancel()
        try:
            await asyncio.wait_for(heartbeat_task, timeout=1.0)
        except (TimeoutError, asyncio.CancelledError):
            pass

        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    logger.info("worker exit code=%s", exit_code)
    return exit_code


def main() -> None:
    """同步入口（``pyproject.toml [project.scripts]`` 注册点）。"""
    try:
        code = asyncio.run(amain())
    except KeyboardInterrupt:
        code = 0
    os._exit(code)


if __name__ == "__main__":
    main()
