"""ACP 客户端（PRD-AGT-006：启动本地 Agent、展示流式状态与权限请求）。

与 MCP 客户端同样是 stdio + 行分隔 JSON-RPC，但有一个**本质区别**：
连接是**双向**的。MCP 里只有我们发请求、对方回响应；ACP 里 Agent 会主动
往回发：

- ``session/update`` 通知 —— 流式进度（思考、工具调用、文本增量）；
- ``session/request_permission`` 请求 —— 要做危险动作前问我们准不准。

所以不能像 MCP 那样「发一条读一条」，必须持续读取并分流：响应给等待中的
调用方，通知交给回调，请求交给权限处理器并回包。

SYSTEM_SPEC §13.6 的约束在这里落地：

- Session 绑定 Project；
- **Agent 不直接读整个 Workspace**，``session/new`` 必须带 ``cwd``
  作为 Root/Scope，越界由 Agent 侧遵守、我们侧记录；
- 可向 Agent 提供本地 STEPWORK MCP Server（``mcpServers`` 参数）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("worker.runtime")

#: ACP 协议版本
PROTOCOL_VERSION = 1

CLIENT_NAME = "stepwork-acp-client"
CLIENT_VERSION = "0.1.0"

#: 单次请求超时（秒）。Agent 干活可能很久，故比 MCP 宽松。
DEFAULT_TIMEOUT = 120.0

#: 单行响应上限（同 MCP：asyncio 默认 64KB 对 Agent 输出远远不够）
MAX_LINE_BYTES = 4 * 1024 * 1024

#: 权限响应的 outcome 取值。
#:
#: 注意 ``cancelled`` 的语义是「**整轮对话被取消**」，不是「这次操作被拒绝」——
#: 协议要求拒绝也走 ``selected``，只是挑一个 ``kind`` 为 reject_* 的选项。
#: 早先把拒绝写成 cancelled 是错的：真实 Agent 会以为用户中止了整轮。
OUTCOME_SELECTED = "selected"
OUTCOME_CANCELLED = "cancelled"

#: PermissionOption.kind 的取值，按「优先级从高到低」分组。
#: 一律优先选 *_once：allow_always / reject_always 会让 Agent **记住**决定，
#: 相当于替用户做了长期授权，不能由我们单方面替他选。
_ALLOW_KINDS = ("allow_once", "allow_always")
_REJECT_KINDS = ("reject_once", "reject_always")


def build_permission_outcome(params: dict[str, Any], allowed: bool) -> dict[str, Any]:
    """按 Agent 给出的 ``options`` 构造权限响应。

    ``optionId`` **必须**取自对方提供的选项列表 —— 自己编一个（早先硬写
    ``"allow"``）真实 Agent 认不出来。按 ``kind`` 匹配意图，找不到合适选项
    时才退回 ``cancelled``（此时确实没有能表达该意图的选项）。
    """
    options = [o for o in (params.get("options") or []) if isinstance(o, dict)]
    wanted = _ALLOW_KINDS if allowed else _REJECT_KINDS
    for kind in wanted:
        for option in options:
            if option.get("kind") == kind and option.get("optionId"):
                return {"outcome": OUTCOME_SELECTED, "optionId": str(option["optionId"])}
    return {"outcome": OUTCOME_CANCELLED}

#: ``session/update`` 里我们关心的进度种类
UpdateHandler = Callable[[dict[str, Any]], Awaitable[None]]
#: 权限处理器：收到请求 → 返回是否放行
PermissionHandler = Callable[[dict[str, Any]], Awaitable[bool]]


class AcpClientError(RuntimeError):
    """与本地 ACP Agent 交互失败。"""

    def __init__(self, code: str, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def _child_env() -> dict[str, str]:
    """同 MCP 客户端：强制 Python 子进程用 UTF-8 读写 stdio。"""
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


class AcpSession:
    """一个 ACP 会话：子进程 + 双向消息泵。

    与 MCP 的短连接不同，ACP 必须**长连接** —— 会话是有状态的（上下文、
    已授权的操作），每次 prompt 重开进程就等于每次都从零开始。
    """

    #: 会话**绑定创建它的事件循环**（子进程管道挂在该循环的 transport 上）。
    #: worker 全程只有一个循环，所以跨命令复用没问题；但循环换了（worker
    #: 重启）之后旧会话不可用 —— 那时给明确报错，而不是让调用方撞上
    #: 「Event loop is closed」这种莫名其妙的异常。
    _loop: asyncio.AbstractEventLoop | None

    def __init__(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        on_update: UpdateHandler | None = None,
        on_permission: PermissionHandler | None = None,
    ) -> None:
        self._loop = None
        self._argv = argv
        self._cwd = cwd
        self._timeout = timeout
        self._on_update = on_update
        self._on_permission = on_permission
        self._proc: asyncio.subprocess.Process | None = None
        self._pump: asyncio.Task[None] | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.session_id: str = ""
        #: 收到的流式更新（供调用方在 prompt 返回后一并取走）
        self.updates: list[dict[str, Any]] = []

    # ---- 生命周期 ----

    async def start(self) -> dict[str, Any]:
        """拉起子进程并完成 ``initialize`` 握手。"""
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_child_env(),
                cwd=self._cwd or None,
                limit=MAX_LINE_BYTES,
            )
        except (OSError, ValueError) as e:
            raise AcpClientError(
                "ACP_SPAWN_FAILED", f"无法启动本地 Agent：{e}"
            ) from e
        self._loop = asyncio.get_running_loop()
        self._pump = asyncio.create_task(self._read_loop())
        return await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )

    async def new_session(
        self, cwd: str, mcp_servers: list[dict[str, Any]] | None = None
    ) -> str:
        """``session/new``：建会话并锁定 Root/Scope。

        ``cwd`` 是 §13.6 的硬约束 —— Agent 只应在这个根下活动，绝不给整个
        Workspace。调用方（handler）负责把它限定到项目目录。
        """
        result = await self._request(
            "session/new", {"cwd": cwd, "mcpServers": mcp_servers or []}
        )
        self.session_id = str(result.get("sessionId") or "")
        if not self.session_id:
            raise AcpClientError("ACP_NO_SESSION", "Agent 未返回 sessionId")
        return self.session_id

    async def prompt(self, text: str) -> dict[str, Any]:
        """``session/prompt``：发一轮提示，期间流式更新走 ``on_update``。"""
        if not self.session_id:
            raise AcpClientError("ACP_NO_SESSION", "尚未建立会话")
        return await self._request(
            "session/prompt",
            {
                "sessionId": self.session_id,
                "prompt": [{"type": "text", "text": text}],
            },
        )

    async def close(self) -> None:
        """收尾：停消息泵、合 stdin、超时强杀。"""
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
            self._pump = None
        # 未完成的等待要有明确结局，否则调用方永远挂着
        for future in self._pending.values():
            if not future.done():
                future.set_exception(
                    AcpClientError("ACP_CLOSED", "会话已关闭")
                )
        self._pending.clear()

        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        try:
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except (TimeoutError, ProcessLookupError, ConnectionResetError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        except RuntimeError:
            # 循环已关闭时 stdin.close() 会抛「Event loop is closed」；
            # 此时进程会被 OS 回收，没什么可做的，也不值得刷栈
            logger.debug("acp close on a closed loop; skipping graceful shutdown")
        except Exception:  # noqa: BLE001 - 收尾失败不覆盖业务异常
            logger.exception("acp session close failed")
        finally:
            transport = getattr(proc, "_transport", None)
            if transport is not None:
                with contextlib.suppress(Exception):
                    transport.close()

    # ---- 消息泵 ----

    async def _read_loop(self) -> None:
        """持续读 stdout，把响应/通知/反向请求分流。"""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        while True:
            try:
                raw = await proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                logger.warning("acp line exceeded %d bytes; dropping", MAX_LINE_BYTES)
                continue
            except asyncio.CancelledError:
                raise
            if not raw:
                self._fail_pending(
                    AcpClientError("ACP_EOF", "Agent 在响应前退出")
                )
                return
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                # Agent 往 stdout 混打日志是常态，跳过而不是判协议错
                logger.debug("acp skipped non-json stdout line")
                continue
            if isinstance(msg, dict):
                await self._route(msg)

    def _fail_pending(self, exc: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    async def _route(self, msg: dict[str, Any]) -> None:
        msg_id = msg.get("id")
        method = msg.get("method")

        # 1) 我们发出去的请求的响应
        if method is None and msg_id is not None:
            future = self._pending.pop(int(msg_id), None) if isinstance(msg_id, int) else None
            if future is not None and not future.done():
                future.set_result(msg)
            return

        # 2) Agent 主动发来的通知（无 id）：流式进度
        if method == "session/update" and msg_id is None:
            params = msg.get("params") or {}
            self.updates.append(params)
            if self._on_update is not None:
                with contextlib.suppress(Exception):
                    await self._on_update(params)
            return

        # 3) Agent 主动发来的请求（有 id）：权限
        if method == "session/request_permission" and msg_id is not None:
            params = msg.get("params") or {}
            allowed = False
            if self._on_permission is not None:
                try:
                    allowed = await self._on_permission(params)
                except Exception:  # noqa: BLE001 - 处理器出错按拒绝处理
                    logger.exception("acp permission handler failed")
                    allowed = False
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"outcome": build_permission_outcome(params, allowed)},
                }
            )
            return

        # 4) 其它反向请求：明确回「不支持」，不能装死让 Agent 挂着
        if msg_id is not None:
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"method not supported: {method}"},
                }
            )

    async def _send(self, obj: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("acp send failed: agent disconnected")

    def _check_loop(self) -> None:
        """会话必须在创建它的循环上使用（见类属性 ``_loop`` 的说明）。"""
        if self._loop is None:
            return
        current = asyncio.get_running_loop()
        if current is not self._loop or self._loop.is_closed():
            raise AcpClientError(
                "ACP_SESSION_STALE",
                "会话所属的事件循环已失效（worker 重启过？），请重新开始会话",
            )

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        proc = self._proc
        if proc is None:
            raise AcpClientError("ACP_NOT_STARTED", "ACP 会话未启动")
        self._check_loop()
        self._next_id += 1
        msg_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        await self._send(
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        )
        try:
            resp = await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError as e:
            self._pending.pop(msg_id, None)
            stderr = await self._stderr_tail()
            raise AcpClientError(
                "ACP_TIMEOUT",
                f"Agent 在 {self._timeout:g}s 内无响应（{method}）",
                detail={"stderr": stderr},
            ) from e
        if "error" in resp:
            err = resp["error"] or {}
            raise AcpClientError(
                "ACP_RPC_ERROR",
                str(err.get("message") or "Agent 返回错误"),
                detail={"code": err.get("code")},
            )
        result = resp.get("result")
        return result if isinstance(result, dict) else {}

    async def _stderr_tail(self) -> str:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return ""
        try:
            data = await asyncio.wait_for(proc.stderr.read(4096), timeout=0.5)
        except (TimeoutError, Exception):  # noqa: BLE001
            return ""
        return data.decode("utf-8", errors="replace").strip()


def summarize_updates(updates: list[dict[str, Any]]) -> str:
    """把流式更新拼成可读文本（供留痕与 UI 回放）。"""
    parts: list[str] = []
    for update in updates:
        # 更新体可能裹在 ``update`` 键里，也可能就是顶层对象
        inner = update.get("update")
        payload: dict[str, Any] = inner if isinstance(inner, dict) else update
        kind = str(payload.get("sessionUpdate") or payload.get("kind") or "")
        content = payload.get("content")
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            parts.append(content["text"])
        elif kind:
            parts.append(f"[{kind}]")
    return "".join(parts)
