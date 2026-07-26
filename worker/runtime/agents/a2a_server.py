"""A2A Server（PRD-AGT-005 的入站方向）。

对外提供两个端点：

- ``GET /.well-known/agent.json`` —— Agent Card（能力发现）；
- ``POST /`` —— JSON-RPC ``message/send``，把 Skill 请求转成一条
  Command Bus 命令执行。

**默认不监听**（SYSTEM_SPEC §8.2「仅在用户开启远程 Agent 服务后监听
HTTP(S)」）。由 ``StartA2aServer`` 命令显式拉起，只绑 127.0.0.1。

权限模型刻意**不在这一层自建**：请求转成 ``source="a2a"`` /
``actor.type="agent"`` 的信封走同一条总线，于是默认拒绝清单、§9.1 审批
降级、审计留痕全部自动生效。这一层只做两件额外的事：

1. **Skill 白名单**：只有 ``a2a_card.SKILLS`` 里登记的 Skill 可达，
   因此 Publisher Execute 类命令根本无从触及（§13.5 的硬要求）；
2. **令牌鉴权**：即使只绑 127.0.0.1，本机其它进程也够得着 —— 这正是
   PRD §9.1 的威胁模型，所以启动时生成随机令牌。
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from worker.runtime.agents.a2a_card import CARD_PATH, build_agent_card, resolve_skill_command

logger = logging.getLogger("worker.runtime")

#: 请求体大小上限，防止超大 body 撑爆内存
MAX_BODY_BYTES = 4 * 1024 * 1024

#: 只绑回环地址：A2A Server 绝不对外网直接暴露
BIND_HOST = "127.0.0.1"


class A2aServerState:
    """当前进程内唯一的 A2A Server 实例状态。"""

    def __init__(self) -> None:
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int = 0
        self.token: str = ""

    @property
    def running(self) -> bool:
        return self.httpd is not None

    @property
    def base_url(self) -> str:
        return f"http://{BIND_HOST}:{self.port}" if self.running else ""


#: 进程级单例（A2A Server 同时只应有一个）
STATE = A2aServerState()

#: 由 :func:`start` 注入：``(command_type, payload) -> CommandResult dict``
_Executor = Any
_executor: _Executor = None


def _make_handler() -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # 静默，避免污染 worker 日志
            return

        def _send(self, status: int, obj: dict[str, Any]) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization") or ""
            _, _, token = header.partition("Bearer ")
            # 常数时间比较，避免令牌被逐字节试探
            return bool(token) and secrets.compare_digest(token.strip(), STATE.token)

        def do_GET(self) -> None:
            # Agent Card 有意**不鉴权**：能力发现是 A2A 的公开握手第一步，
            # 且卡片只含技能描述，不含任何项目数据或凭据。
            if urlparse(self.path).path == CARD_PATH:
                self._send(200, build_agent_card(STATE.base_url))
                return
            self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._authorized():
                self._send(401, _rpc_error(None, -32001, "unauthorized"))
                return
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                self._send(400, _rpc_error(None, -32600, "bad Content-Length"))
                return
            if length > MAX_BODY_BYTES:
                self._send(413, _rpc_error(None, -32600, "body too large"))
                return
            try:
                raw = self.rfile.read(length) if length else b"{}"
                req = json.loads(raw or b"{}")
            except (ValueError, OSError) as exc:
                self._send(400, _rpc_error(None, -32700, f"parse error: {exc}"))
                return
            if not isinstance(req, dict):
                self._send(400, _rpc_error(None, -32600, "request is not an object"))
                return
            self._send(200, _handle_rpc(req))

    return _Handler


def _rpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _extract_text(message: dict[str, Any]) -> str:
    """A2A Message → 纯文本（只取 text part）。"""
    parts = []
    for part in message.get("parts") or []:
        if isinstance(part, dict) and part.get("kind") == "text":
            parts.append(str(part.get("text") or ""))
    return "\n".join(parts)


def _handle_rpc(req: dict[str, Any]) -> dict[str, Any]:
    msg_id = req.get("id")
    method = req.get("method")
    if method != "message/send":
        return _rpc_error(msg_id, -32601, f"method not found: {method}")

    params = req.get("params") or {}
    message = params.get("message") if isinstance(params, dict) else None
    if not isinstance(message, dict):
        return _rpc_error(msg_id, -32602, "params.message required")

    metadata = params.get("metadata") or {}
    skill_id = str(metadata.get("skillId") or "") if isinstance(metadata, dict) else ""
    command = resolve_skill_command(skill_id)
    if command is None:
        # 白名单之外一律拒绝 —— 这是「不暴露 Publisher Execute」的落点
        return _rpc_error(
            msg_id, -32602, f"unknown or unsupported skill: {skill_id or '(missing)'}"
        )

    text = _extract_text(message)
    context_id = str(message.get("contextId") or "") or uuid.uuid4().hex
    try:
        result = _executor(command, {"text": text, "skillId": skill_id})
    except Exception as exc:  # noqa: BLE001 - 任何执行异常都转成 RPC 错误
        logger.exception("a2a skill execution failed skill=%s", skill_id)
        return _rpc_error(msg_id, -32603, f"internal error: {exc}")

    ok = bool(result.get("ok"))
    task_id = str(result.get("job_id") or result.get("commandId") or uuid.uuid4().hex)
    # A2A Task ← AgentTask，Artifact ← AgentArtifact（SYSTEM_SPEC §13.5）
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": {
            "id": task_id,
            "contextId": context_id,
            "kind": "task",
            "status": {"state": "completed" if ok else "failed"},
            "artifacts": [
                {
                    "artifactId": uuid.uuid4().hex,
                    "name": skill_id,
                    "parts": [
                        {
                            "kind": "text",
                            "text": json.dumps(
                                result.get("detail") or {}, ensure_ascii=False
                            ),
                        }
                    ],
                }
            ]
            if ok
            else [],
            "metadata": {"error": result.get("error")} if not ok else {},
        },
    }


def start(executor: Any, port: int = 0) -> A2aServerState:
    """启动 A2A Server（幂等：已在跑则直接返回当前状态）。

    Args:
        executor: ``(command_type, payload) -> CommandResult dict``，由
            handler 注入，内部走 Command Bus。
        port: 0 表示由系统分配空闲端口（测试与桌面端都用这个，避免撞端口）。
    """
    global _executor
    if STATE.running:
        return STATE
    _executor = executor
    STATE.token = secrets.token_urlsafe(32)
    httpd = ThreadingHTTPServer((BIND_HOST, port), _make_handler())
    STATE.httpd = httpd
    STATE.port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="a2a-server")
    thread.start()
    STATE.thread = thread
    logger.info("a2a server listening on %s", STATE.base_url)
    return STATE


def stop() -> bool:
    """停止 Server；返回是否确实停了一个。"""
    global _executor
    httpd = STATE.httpd
    if httpd is None:
        return False
    httpd.shutdown()
    httpd.server_close()
    if STATE.thread is not None:
        STATE.thread.join(timeout=3.0)
    STATE.httpd = None
    STATE.thread = None
    STATE.port = 0
    STATE.token = ""
    _executor = None
    return True


async def stop_async() -> bool:
    """在事件循环里安全地停 Server（``shutdown()`` 会阻塞，扔到线程池）。"""
    return await asyncio.get_running_loop().run_in_executor(None, stop)
