"""出站 HTTP 客户端的统一构造（含代理策略与共享连接）。

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

**共享 AsyncClient（P1 效率）**

``httpx.AsyncClient`` 每次新建 = 一次新的连接池 + 一次 TLS 握手 + 上述代理
探测。此前 ai/asr/tts/image/a2a 五个 Provider 每请求 ``async with
make_async_client() as c:`` —— 一条 8 幕视频 TTS = 8 次握手 + 8 次探测，
探测本身还跑在事件循环上。修法：

- 代理探测结果按 TTL 缓存（默认 5 分钟），同一次进程里只真探一次；
- :func:`shared_async_client` 提供进程级共享实例，各 Provider 复用连接；
- worker 关停时 :func:`aclose_shared_async_client` 释放（bootstrap/__main__ 挂钩）。
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
import urllib.parse
import urllib.request
from typing import Any

import httpx

logger = logging.getLogger("worker.runtime.net")

#: 探测代理端口是否有人监听的超时。端口没人监听时会立刻 RST，等不到这么久。
_PROBE_TIMEOUT = 0.25

#: 代理存活判定的 TTL 缓存。0.25s 探测看似便宜，但**同步阻塞事件循环**；
#: AI/TTS/ASR 每条请求都探一次 = 每条请求白白多 250ms。5 分钟粒度对用户
#: 无感（切换代理软件本来就需要几秒生效），却能挡掉绝大多数重复探测。
_PROBE_TTL_SEC: float = 300.0

#: 已提醒过的死代理（一个进程里同一条代理只提醒一次：配一次音要建 N 个客户端，
#: 刷 N 条同样的 warning 会把别的日志淹掉）
_warned: set[str] = set()

# 代理判定缓存：(monotonic_ts, is_dead)。多协程并发进入时用一个锁串行化探测，
# 后到的走缓存；比 ``functools.lru_cache`` + 计时要简洁，且支持 TTL 过期。
_probe_cache: tuple[float, bool] | None = None
_probe_lock: asyncio.Lock | None = None

# 进程级共享 AsyncClient（懒建，shutdown 时经 aclose_shared_async_client 释放）
_shared_client: httpx.AsyncClient | None = None
_shared_client_lock: asyncio.Lock | None = None


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


def _proxy_is_dead_uncached() -> bool:
    """原始探测逻辑（不查缓存）：配了代理但连不上 → True。"""
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


def proxy_is_dead() -> bool:
    """配了代理但连不上 → ``True``（调用方应改走直连）。

    带 TTL 缓存 —— 见 :data:`_PROBE_TTL_SEC`。首次调用同步阻塞 ~0.25s（端口
    RST 通常 <10ms），后续 5 分钟内直接命中缓存。测试与热路径都以同步入口
    为主；协程里想彻底避免那次 0.25s 请走 :func:`proxy_is_dead_async`。
    """
    global _probe_cache
    now = time.monotonic()
    if _probe_cache is not None and now - _probe_cache[0] < _PROBE_TTL_SEC:
        return _probe_cache[1]
    dead = _proxy_is_dead_uncached()
    _probe_cache = (now, dead)
    return dead


async def proxy_is_dead_async() -> bool:
    """协程入口：把 socket 探测放线程池，避免首次 0.25s 冻结事件循环。

    与 :func:`proxy_is_dead` 共用同一份 TTL 缓存 —— 只要进程里任一路径探
    过，其它路径直接命中。给 bootstrap / provider 协程里的调用使用。
    """
    global _probe_cache, _probe_lock
    if _probe_lock is None:
        _probe_lock = asyncio.Lock()
    async with _probe_lock:
        now = time.monotonic()
        if _probe_cache is not None and now - _probe_cache[0] < _PROBE_TTL_SEC:
            return _probe_cache[1]
        dead = await asyncio.to_thread(_proxy_is_dead_uncached)
        _probe_cache = (now, dead)
        return dead


def reset_proxy_probe_cache() -> None:
    """清 TTL 缓存。测试与用户显式改代理设置时调用。"""
    global _probe_cache
    _probe_cache = None


#: 共享 AsyncClient 的默认超时；单个请求可用 ``timeout=`` 覆盖。
_DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


async def shared_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """进程级共享 :class:`httpx.AsyncClient`（懒建 + 复用连接）。

    Providers（ai/asr/tts/image/a2a）此前每请求 :func:`make_async_client` ——
    一条 8 幕 TTS = 8 次 TLS 握手 + 8 次代理探测。共享实例把连接池、TLS
    session、HTTP/2 都留着复用。

    - ``kwargs`` 只在**首次**建实例时生效（后续调用忽略，防止互相覆盖）；
    - 每个请求可独立传 ``timeout=`` / ``follow_redirects=``；
    - worker 关停时 :func:`aclose_shared_async_client` 释放（bootstrap 挂钩）。
    """
    global _shared_client, _shared_client_lock
    if _shared_client_lock is None:
        _shared_client_lock = asyncio.Lock()
    async with _shared_client_lock:
        if _shared_client is None or _shared_client.is_closed:
            _shared_client = make_async_client(
                timeout=kwargs.pop("timeout", _DEFAULT_TIMEOUT),
                follow_redirects=kwargs.pop("follow_redirects", True),
                **kwargs,
            )
        return _shared_client


async def aclose_shared_async_client() -> None:
    """关停共享 client（bootstrap 优雅退出时调，或测试用例里重置）。"""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


def make_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """构造**一次性** :class:`httpx.AsyncClient`；代理配了却连不上时退回直连。

    显式传入 ``trust_env`` 时**完全尊重调用方**（测试与特殊部署需要这个口子）。
    热路径请优先 :func:`shared_async_client` —— 每次 new 都要重新握手 + 探代理。
    """
    if "trust_env" not in kwargs and proxy_is_dead():
        kwargs["trust_env"] = False
    return httpx.AsyncClient(**kwargs)
