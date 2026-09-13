"""出站客户端代理策略测试。

对应真机缺陷（2026-09-13）：Windows 注册表里的「系统代理」在用户关掉
Clash 之后仍然留着，``httpx`` 默认 ``trust_env=True`` 会把每个出站请求
送进那个没人监听的端口 —— AI / TTS / ASR / 图像 / 下载全线报
``ConnectError: All connection attempts failed``，现场极像断网。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from worker.runtime import net

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _proxies(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, str]) -> None:
    monkeypatch.setattr(net, "_configured_proxies", lambda: mapping)


def _reach(monkeypatch: pytest.MonkeyPatch, alive: bool) -> list[tuple[str, int]]:
    """把端口探测换成假实现，并返回调用记录。"""
    calls: list[tuple[str, int]] = []

    def fake_reachable(host: str, port: int) -> bool:
        calls.append((host, port))
        return alive

    monkeypatch.setattr(net, "_reachable", fake_reachable)
    return calls


# -- 代理串解析 -------------------------------------------------------------


def test_proxy_endpoint_accepts_registry_forms() -> None:
    # 注册表里存的常常**不带 scheme**
    assert net._proxy_endpoint("127.0.0.1:7897") == ("127.0.0.1", 7897)
    assert net._proxy_endpoint("http://127.0.0.1:7897") == ("127.0.0.1", 7897)
    # 不带端口时按 scheme 补默认端口
    assert net._proxy_endpoint("http://proxy.lan") == ("proxy.lan", 80)
    assert net._proxy_endpoint("https://proxy.lan") == ("proxy.lan", 443)


def test_proxy_endpoint_returns_none_when_unsure() -> None:
    # 拿不准就别下结论：判死会静默改走直连，代价比误判成「代理是好的」大
    assert net._proxy_endpoint("") is None
    assert net._proxy_endpoint("127.0.0.1:not-a-port") is None
    assert net._proxy_endpoint("http://") is None


# -- 死代理判据 -------------------------------------------------------------


def test_no_proxy_configured_means_not_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    _proxies(monkeypatch, {})
    probed = _reach(monkeypatch, alive=True)
    assert net.proxy_is_dead() is False
    assert probed == [], "根本没配代理，不该去探测端口"


def test_dead_proxy_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    _proxies(monkeypatch, {"https": "http://127.0.0.1:7897"})
    probed = _reach(monkeypatch, alive=False)
    assert net.proxy_is_dead() is True
    assert probed == [("127.0.0.1", 7897)], "必须真的探到那一个代理端点"


def test_live_proxy_is_not_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    _proxies(monkeypatch, {"https": "http://127.0.0.1:7897"})
    _reach(monkeypatch, alive=True)
    assert net.proxy_is_dead() is False


def test_unparseable_proxy_is_not_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    _proxies(monkeypatch, {"https": "127.0.0.1:not-a-port"})
    probed = _reach(monkeypatch, alive=False)
    assert net.proxy_is_dead() is False
    assert probed == [], "端点都拆不出来，不该据此把代理判死"


def test_falls_back_to_http_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    # 只配了 http 代理（很常见）时同样要判 —— 否则判据形同虚设
    _proxies(monkeypatch, {"http": "127.0.0.1:7897"})
    probed = _reach(monkeypatch, alive=False)
    assert net.proxy_is_dead() is True
    assert probed == [("127.0.0.1", 7897)]


def test_dead_proxy_warns_only_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(net, "_warned", set())
    _proxies(monkeypatch, {"https": "http://127.0.0.1:7897"})
    _reach(monkeypatch, alive=False)
    with caplog.at_level(logging.WARNING, logger="worker.runtime.net"):
        net.proxy_is_dead()
        net.proxy_is_dead()
    assert len(caplog.records) == 1, (
        "一条死代理只该提醒一次 —— 配一次音会建 N 个客户端，刷屏会淹掉别的日志"
    )
    assert "127.0.0.1:7897" in caplog.records[0].getMessage()


# -- 客户端构造 -------------------------------------------------------------


def test_client_drops_proxy_when_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net, "proxy_is_dead", lambda: True)
    client = net.make_async_client(timeout=5.0)
    assert client.trust_env is False, "代理死了还 trust_env 就会把请求打进死端口"
    assert client.timeout.read == 5.0, "其余参数必须原样透传"


def test_client_keeps_env_when_proxy_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net, "proxy_is_dead", lambda: False)
    client = net.make_async_client()
    assert client.trust_env is True, "代理是好的就不能拦掉用户的代理设置"


def test_explicit_trust_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net, "proxy_is_dead", lambda: True)
    client = net.make_async_client(trust_env=True)
    assert client.trust_env is True, "调用方显式指定时不该被覆盖"


# -- 结构性护栏 -------------------------------------------------------------


def test_no_raw_httpx_client_outside_net() -> None:
    """出站客户端必须走 ``make_async_client`` —— 直接构造会绕过死代理判据。

    这条护栏的价值不在当前代码，而在**以后**：再有人写
    ``httpx.AsyncClient()`` 就等于把这个坑重新挖一遍，且没有任何报错。
    """
    runtime = _REPO_ROOT / "worker" / "runtime"
    offenders: list[str] = []
    for path in sorted(runtime.rglob("*.py")):
        if path.name == "net.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "httpx.AsyncClient(" in source or "httpx.Client(" in source:
            offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert offenders == [], (
        "这些文件直接构造 httpx 客户端，绕过了死代理判据："
        f"{offenders} —— 请改用 worker.runtime.net.make_async_client"
    )
