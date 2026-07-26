"""MCP 客户端（PRD-AGT-004：可连接外部搜索或知识库 Server）。

STEPWORK 此前只有**入站** MCP（``mcp/server.py``，外部 Agent 调我们）。
本模块是**出站**：把外部 MCP Server 当工具用 —— 典型场景是接一个搜索
或知识库 Server，给选题/脚本阶段补充事实依据。

传输：stdio + 行分隔 JSON-RPC 2.0，与 ``mcp/server.py`` 同一套框架，
故本仓自带的 Server 可直接作为被连对象（测试正是这么做的，不依赖任何
第三方 Server 就能端到端验证）。

安全边界（PRD §9、§11.3）：

- **不自动发现、不自动连接**：只连用户在 Agent Connections 页显式登记
  的命令行；命令与参数原样落 ``agent_connections.endpoint_or_command``。
- **子进程隔离**：不继承本进程 stdin，超时强杀，退出码与 stderr 一并
  回报，避免挂死在一个坏 Server 上。
- **外部结果不可信**：返回内容一律按 ``external-unverified`` 处理，由
  ``agent_record`` 落 ``pending_review``，绝不直接进正文。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from typing import Any

logger = logging.getLogger("worker.runtime")

#: MCP 协议版本（与 ``mcp/server.py`` 保持一致）
PROTOCOL_VERSION = "2024-11-05"

#: 客户端自报身份
CLIENT_NAME = "stepwork-mcp-client"
CLIENT_VERSION = "0.1.0"

#: 单次请求超时（秒）。外部 Server 挂了不能把 worker 拖住。
DEFAULT_TIMEOUT = 20.0

#: 单次响应最大字节数。外部 Server 可能吐出超大结果（甚至恶意撑爆内存），
#: 超限即断开并报错，而不是无限读。
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def _child_env() -> dict[str, str]:
    """子进程环境：继承当前环境，但强制 Python 子进程用 UTF-8 读写 stdio。

    MCP 规定传输层是 UTF-8，但 Windows 上 Python 的 stdout 默认走**本地
    代码页**（简体中文机器上是 cp936）。绝大多数 MCP Server 是 Python
    写的，于是含中文的结果会在管道里被写成 cp936，我们按 UTF-8 解码就
    变成乱码 —— 这不是假设，是本模块测试里实际撞到的。
    ``PYTHONIOENCODING`` 只对 Python 子进程生效，其它语言的 Server 会忽略
    它（它们本来就用 UTF-8），因此这是零副作用的兜底。
    """
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


class McpClientError(RuntimeError):
    """与外部 MCP Server 交互失败（连不上、超时、协议错误、工具报错）。"""

    def __init__(self, code: str, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def parse_command(endpoint_or_command: str) -> list[str]:
    """把登记的命令行拆成 argv。

    用 ``shlex.split(posix=False)``：Windows 路径里的反斜杠不能被当成
    转义符吃掉（``C:\\Tools\\srv.exe`` 必须原样保留）。
    """
    argv = shlex.split(endpoint_or_command, posix=False)
    if not argv:
        raise McpClientError("MCP_CLIENT_BAD_COMMAND", "连接命令为空")
    # shlex(posix=False) 会保留包裹的引号，需自行剥离
    return [a[1:-1] if len(a) > 1 and a[0] == a[-1] and a[0] in "\"'" else a for a in argv]


class McpStdioClient:
    """一次会话：拉起子进程 → initialize → 若干请求 → 关闭。

    刻意做成**短连接**（每条命令一进程），而不是常驻连接池：桌面端调用
    频率低，常驻进程会带来「用户关了连接但进程还活着」的一致性问题，
    也让崩溃恢复变复杂。代价是每次多一次进程启动，可接受。
    """

    def __init__(self, argv: list[str], *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._argv = argv
        self._timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0

    async def __aenter__(self) -> McpStdioClient:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_child_env(),
                # asyncio 的 StreamReader 默认单行上限是 64KB，超了 readline()
                # 直接抛 ValueError。MCP 的搜索/读文件类结果轻易超过 64KB，
                # 用默认值等于「稍大的响应就崩」。抬到与 MAX_RESPONSE_BYTES
                # 一致，真超限时由 _read_response 给出明确错误码。
                limit=MAX_RESPONSE_BYTES,
            )
        except (OSError, ValueError) as e:
            raise McpClientError(
                "MCP_CLIENT_SPAWN_FAILED", f"无法启动 MCP Server：{e}"
            ) from e
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """关闭子进程；先合上 stdin 让对方自然退出，超时再强杀。"""
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        try:
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except (TimeoutError, ProcessLookupError, ConnectionResetError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        except Exception:  # noqa: BLE001 - 收尾失败不该覆盖业务异常
            logger.exception("mcp client close failed")
        finally:
            # 进程退出后 stdout/stderr 的读管道仍挂在 transport 上，等 GC
            # 才关 —— Windows 的 Proactor 会在 __del__ 里抛
            # "unclosed transport"。显式关掉，避免长期运行下累积句柄。
            transport = getattr(proc, "_transport", None)
            if transport is not None:
                try:
                    transport.close()
                except Exception:  # noqa: BLE001 - 已关闭/已释放均可忽略
                    logger.debug("mcp client transport already closed")

    async def _stderr_tail(self) -> str:
        """读一段 stderr 作为诊断信息（Server 崩溃时最有用的线索）。"""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return ""
        try:
            data = await asyncio.wait_for(proc.stderr.read(4096), timeout=0.5)
        except (TimeoutError, Exception):  # noqa: BLE001
            return ""
        return data.decode("utf-8", errors="replace").strip()

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        """发一条 JSON-RPC 请求并等待同 id 的响应。"""
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise McpClientError("MCP_CLIENT_NOT_STARTED", "MCP 客户端未启动")

        self._next_id += 1
        msg_id = self._next_id
        line = json.dumps(
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params},
            ensure_ascii=False,
        )
        try:
            proc.stdin.write(line.encode("utf-8") + b"\n")
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            stderr = await self._stderr_tail()
            raise McpClientError(
                "MCP_CLIENT_DISCONNECTED",
                f"MCP Server 已断开：{e}",
                detail={"stderr": stderr},
            ) from e

        try:
            resp = await asyncio.wait_for(
                self._read_response(proc.stdout, msg_id), timeout=self._timeout
            )
        except TimeoutError as e:
            stderr = await self._stderr_tail()
            raise McpClientError(
                "MCP_CLIENT_TIMEOUT",
                f"MCP Server 在 {self._timeout:g}s 内无响应（{method}）",
                detail={"stderr": stderr},
            ) from e

        if "error" in resp:
            err = resp["error"] or {}
            raise McpClientError(
                "MCP_CLIENT_RPC_ERROR",
                str(err.get("message") or "MCP Server 返回错误"),
                detail={"code": err.get("code")},
            )
        return resp.get("result")

    async def _read_response(self, stdout: asyncio.StreamReader, msg_id: int) -> dict[str, Any]:
        """读到 id 匹配的响应为止；跳过通知与不匹配的行。"""
        read_bytes = 0
        while True:
            try:
                raw = await stdout.readline()
            except (ValueError, asyncio.LimitOverrunError) as e:
                # 单行超过 limit：asyncio 抛的是裸 ValueError，必须转成
                # 我们自己的错误码，否则调用方看到的是不可辨认的异常
                raise McpClientError(
                    "MCP_CLIENT_RESPONSE_TOO_LARGE",
                    f"MCP Server 单条响应超过 {MAX_RESPONSE_BYTES} 字节上限",
                ) from e
            if not raw:
                stderr = await self._stderr_tail()
                raise McpClientError(
                    "MCP_CLIENT_EOF",
                    "MCP Server 在响应前退出",
                    detail={"stderr": stderr},
                )
            read_bytes += len(raw)
            if read_bytes > MAX_RESPONSE_BYTES:
                raise McpClientError(
                    "MCP_CLIENT_RESPONSE_TOO_LARGE",
                    f"MCP Server 响应超过 {MAX_RESPONSE_BYTES} 字节上限",
                )
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                # 有些 Server 会往 stdout 混打日志；跳过非 JSON 行而不是报错
                logger.debug("mcp client skipped non-json stdout line")
                continue
            if isinstance(msg, dict) and msg.get("id") == msg_id:
                return msg

    async def initialize(self) -> dict[str, Any]:
        """MCP 握手；返回对方的 ``serverInfo`` / ``capabilities``。"""
        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        return result if isinstance(result, dict) else {}

    async def list_tools(self) -> list[dict[str, Any]]:
        """``tools/list``：对方暴露的工具目录。"""
        result = await self._request("tools/list", {})
        tools = (result or {}).get("tools") if isinstance(result, dict) else None
        return [t for t in (tools or []) if isinstance(t, dict)]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """``tools/call``：调用一个工具。返回原始 result 对象。"""
        result = await self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        return result if isinstance(result, dict) else {"content": []}


def flatten_content(result: dict[str, Any]) -> str:
    """把 MCP ``tools/call`` 的 content 块拼成纯文本（供 UI 展示/入库）。

    MCP 的 content 是 ``[{type, text|data...}]``；非文本块只留类型占位，
    绝不把 base64 图片之类整块塞进文本。
    """
    parts: list[str] = []
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        else:
            parts.append(f"[{block.get('type') or 'unknown'}]")
    return "\n".join(parts)
