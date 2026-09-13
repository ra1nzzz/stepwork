"""出站 HTTP 客户端的统一构造（含代理策略）。

**为什么需要这个模块（2026-09-13 本机实测的真实现场）**

``httpx`` 默认 ``trust_env=True``，而 ``trust_env`` 会经
``urllib.request.getproxies()`` 读 **Windows 注册表**里的「系统代理」。用户
装过 Clash / 加速器之后把软件关掉，注册表里的 ``127.0.0.1:7897`` **仍然留着**
—— 于是每个出站请求都被送进一个**没人监听的端口**，报
``ConnectError: All connection attempts failed``。

现场表现极像「断网」：AI（生成选题）、TTS、ASR、图像、媒体下载**全线不可用**，
而同一台机器上 ``trust_env=False`` 直连后百度与 StepFun 双双 HTTP 200。排查
时很容易误判成密钥/端点/网络出口问题，实际只是注册表里的一条代理残留。

**策略：代理照继承，但「配了却连不上」就当没配。**

不选「一刀切 ``trust_env=False``」是因为那会把**确实靠代理访问海外模型端点**
的用户打回不可用；而代理端口都拒绝连接时，走代理是**必然失败**、直连是唯一
可能成功的路径，此时退回直连严格更优。判定只做一次 TCP 连通性探测（0.25s），
端口没人监听时内核会立刻回 RST，代价近似为零。

env 代理（``HTTP_PROXY`` 等）与注册表代理走**同一道判据** —— 残留的 env 代理
比注册表更少见，但同样是「配置在、服务没了」，没有理由区别对待。
"""

from __future__ import annotations

import logging
import socket
import urllib.parse
import urllib.request
from typing import Any

import httpx

logger = logging.getLogger("worker.runtime.net")

#: 探测代理端口是否有人监听的超时。端口没人监听时会立刻 RST，等不到这么久。
_PROBE_TIMEOUT = 0.25

#: 已提醒过的死代理（一个进程里同一条代理只提醒一次：配一次音要建 N 个客户端，
#: 刷 N 条同样的 warning 会把别的日志淹掉）
_warned: set[str] = set()


def _warn_once(raw: str, host: str, port: int) -> None:
    if raw in _warned:
        return
    _warned.add(raw)
    logger.warning(
        "代理 %s 没有进程在监听（%s:%s），本次出站请求改走直连 —— "
        "多半是代理软件关掉了、而系统代理设置还留着",
        raw,
        host,
        port,
    )


def _proxy_endpoint(raw: str) -> tuple[str, int] | None:
    """把代理串拆成 ``(host, port)``；拆不出来时返回 ``None``（**不判死**）。

    拿不准就别下结论：判死会静默改走直连，宁可当成「代理是好的」。
    """
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urllib.parse.urlsplit(raw)
        host = parts.hostname
        port = parts.port
    except ValueError:  # 端口不是数字之类
        return None
    if not host:
        return None
    return host, port or (443 if parts.scheme == "https" else 80)


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def _configured_proxies() -> dict[str, str]:
    """当前生效的代理配置。

    ``urllib.request.getproxies()`` 会把 **env 与 Windows 注册表**合并 ——
    两者都是我们要判的对象，所以直接用它，而不是只读 ``os.environ``。
    """
    try:
        return dict(urllib.request.getproxies())
    except Exception:  # noqa: BLE001 - 注册表读不出来就当「没配代理」
        return {}


def proxy_is_dead() -> bool:
    """配了代理但连不上 → ``True``（调用方应改走直连）。

    没有任何代理配置时返回 ``False``（无事发生，不必探测）。多个 scheme 都配了
    代理时只看第一个能解析出端点的：它们通常指向同一个代理进程。
    """
    proxies = _configured_proxies()
    for scheme in ("https", "http", "all"):
        raw = proxies.get(scheme)
        if not raw:
            continue
        endpoint = _proxy_endpoint(raw)
        if endpoint is None:
            return False
        if _reachable(*endpoint):
            return False
        _warn_once(raw, *endpoint)
        return True
    return False


def make_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """构造出站 :class:`httpx.AsyncClient`；代理配了却连不上时退回直连。

    显式传入 ``trust_env`` 时**完全尊重调用方**（测试与特殊部署需要这个口子）。
    """
    if "trust_env" not in kwargs and proxy_is_dead():
        kwargs["trust_env"] = False
    return httpx.AsyncClient(**kwargs)
