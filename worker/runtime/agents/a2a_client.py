"""A2A 客户端（PRD-AGT-005 的出站方向）。

拉取对方的 Agent Card、向对方发任务。传输是 HTTP + JSON-RPC 2.0
（``message/send``），与 MCP 客户端的 stdio 不同，所以单独一层。

安全边界：

- **只连用户显式登记的地址**，不做网络发现；
- 强制超时与响应大小上限，坏 Agent 不能把 worker 拖住或撑爆；
- 对方 Card / 响应一律当不可信输入解析（见 ``a2a_card.parse_remote_card``）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from worker.runtime.agents.a2a_card import CARD_PATH

#: 单次请求超时（秒）
DEFAULT_TIMEOUT = 20.0

#: 响应大小上限，防止对端用超大 body 撑爆内存
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class A2aClientError(RuntimeError):
    """与远端 A2A Agent 交互失败。"""

    def __init__(self, code: str, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def normalize_base_url(url: str) -> str:
    """校验并归一化对端地址。

    只接受 http / https，且必须有主机名 —— 否则 ``urljoin`` 会把
    ``file:///etc/passwd`` 之类拼出来，变成本地文件读取。
    """
    raw = (url or "").strip()
    if not raw:
        raise A2aClientError("A2A_BAD_URL", "Agent 地址为空")
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise A2aClientError(
            "A2A_BAD_URL", f"仅支持 http/https，收到 {parsed.scheme!r}"
        )
    if not parsed.netloc:
        raise A2aClientError("A2A_BAD_URL", "Agent 地址缺少主机名")
    return raw.rstrip("/")


def _auth_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _post_json(
    url: str, body: dict[str, Any], timeout: float, token: str | None = None
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.post(url, json=body, headers=_auth_headers(token))
    except httpx.TimeoutException as e:
        raise A2aClientError("A2A_TIMEOUT", f"远端 Agent 在 {timeout:g}s 内无响应") from e
    except httpx.HTTPError as e:
        raise A2aClientError("A2A_UNREACHABLE", f"无法连接远端 Agent：{e}") from e
    return _decode(resp)


def _decode(resp: httpx.Response) -> dict[str, Any]:
    if len(resp.content) > MAX_RESPONSE_BYTES:
        raise A2aClientError(
            "A2A_RESPONSE_TOO_LARGE",
            f"远端响应超过 {MAX_RESPONSE_BYTES} 字节上限",
        )
    if resp.status_code >= 400:
        raise A2aClientError(
            "A2A_HTTP_ERROR",
            f"远端返回 HTTP {resp.status_code}",
            detail={"body": resp.text[:500]},
        )
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as e:
        raise A2aClientError("A2A_BAD_RESPONSE", "远端响应不是合法 JSON") from e
    if not isinstance(data, dict):
        raise A2aClientError("A2A_BAD_RESPONSE", "远端响应不是 JSON 对象")
    return data


async def fetch_agent_card(
    base_url: str, *, timeout: float = DEFAULT_TIMEOUT, token: str | None = None
) -> dict[str, Any]:
    """GET ``/.well-known/agent.json``。

    多数实现（含我们自己）对 Card 不鉴权，但有些私有部署会要求，故支持
    可选令牌。
    """
    root = normalize_base_url(base_url)
    url = urljoin(f"{root}/", CARD_PATH.lstrip("/"))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.get(url, headers=_auth_headers(token))
    except httpx.TimeoutException as e:
        raise A2aClientError("A2A_TIMEOUT", f"拉取 Agent Card 超时（{timeout:g}s）") from e
    except httpx.HTTPError as e:
        raise A2aClientError("A2A_UNREACHABLE", f"无法拉取 Agent Card：{e}") from e
    return _decode(resp)


async def send_message(
    base_url: str,
    text: str,
    *,
    skill_id: str | None = None,
    context_id: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    token: str | None = None,
) -> dict[str, Any]:
    """A2A ``message/send``；返回 result 对象（Task 或 Message）。"""
    root = normalize_base_url(base_url)
    params: dict[str, Any] = {
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": text}],
            "messageId": uuid.uuid4().hex,
        }
    }
    if skill_id:
        # 非标准但广泛使用的路由提示；对端不认时会忽略
        params["metadata"] = {"skillId": skill_id}
    if context_id:
        params["message"]["contextId"] = context_id

    body = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/send",
        "params": params,
    }
    data = await _post_json(root, body, timeout, token)
    if "error" in data:
        err = data.get("error") or {}
        raise A2aClientError(
            "A2A_RPC_ERROR",
            str(err.get("message") or "远端 Agent 返回错误"),
            detail={"code": err.get("code")},
        )
    result = data.get("result")
    return result if isinstance(result, dict) else {}


def extract_artifact_text(result: dict[str, Any]) -> str:
    """从 A2A Task / Message 结果里抽出可读文本。

    A2A 的结果形状有两种：Task（带 ``artifacts``）或直接 Message
    （带 ``parts``）。两种都要能取到文本，否则调用方得自己分支。
    非文本 part 只留类型占位，不把二进制塞进文本。
    """
    parts: list[str] = []

    def _collect(part_list: Any) -> None:
        for part in part_list or []:
            if not isinstance(part, dict):
                continue
            if part.get("kind") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
            else:
                parts.append(f"[{part.get('kind') or 'unknown'}]")

    for artifact in result.get("artifacts") or []:
        if isinstance(artifact, dict):
            _collect(artifact.get("parts"))
    if not parts:
        _collect(result.get("parts"))
    if not parts:
        status = result.get("status")
        if isinstance(status, dict) and isinstance(status.get("message"), dict):
            _collect(status["message"].get("parts"))
    return "\n".join(parts)
