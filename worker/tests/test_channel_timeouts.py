"""第十轮：Agent 通道公共常量收口的行为锁。

对应评审里 P1 #6 —— ``agents/`` 下 mcp_client / acp_client 各写了一遍
``timeout=3.0`` 关子进程 grace + ``timeout=0.5`` 抓崩溃 stderr 的诊断预算，
"第四个协议进来会变成第三遍"。本批把这两个数字收口到
:mod:`worker.runtime.agents.channel` 的 :data:`CLOSE_WAIT_SEC` /
:data:`STDERR_TAIL_TIMEOUT_SEC`。

**不**统一的是各家 ``DEFAULT_TIMEOUT``（MCP/A2A 20s、ACP 120s）——
差别来自协议语义（ACP 一次 prompt 可能跑几十秒的推理与工具链），
不是随手写的重复常量；channel.py 里已加"为什么这个值故意不合并"的
显式说明，锁测试也确保未来有人试图把它抹平时能看到。
"""

from __future__ import annotations

import re
from pathlib import Path

_CHANNEL = Path("worker/runtime/agents/channel.py")
_MCP = Path("worker/runtime/agents/mcp_client.py")
_ACP = Path("worker/runtime/agents/acp_client.py")


def test_channel_exposes_shared_timing_constants() -> None:
    src = _CHANNEL.read_text(encoding="utf-8")
    assert "CLOSE_WAIT_SEC" in src, "channel.py 缺 CLOSE_WAIT_SEC"
    assert "STDERR_TAIL_TIMEOUT_SEC" in src, "channel.py 缺 STDERR_TAIL_TIMEOUT_SEC"


def test_clients_do_not_hardcode_close_or_stderr_timeouts() -> None:
    """``asyncio.wait_for(proc.wait(), timeout=3.0)`` 与
    ``proc.stderr.read(4096), timeout=0.5)`` 都应收口到 channel 常量。
    字面数字再出现 = 第四家协议又要复制一遍。"""
    for path in (_MCP, _ACP):
        src = path.read_text(encoding="utf-8")
        assert "proc.wait(), timeout=3.0" not in src, (
            f"{path.name} 又出现硬编码 3.0s close grace —— 请用 CLOSE_WAIT_SEC"
        )
        assert re.search(
            r"stderr\.read\([^)]+\),\s*timeout=0\.5", src
        ) is None, (
            f"{path.name} 里 proc.stderr.read 又用了硬编码 0.5s —— "
            "请用 STDERR_TAIL_TIMEOUT_SEC"
        )


def test_clients_import_from_channel() -> None:
    for path in (_MCP, _ACP):
        src = path.read_text(encoding="utf-8")
        assert "from worker.runtime.agents.channel import" in src, (
            f"{path.name} 未从 agents.channel import 常量"
        )


def test_protocol_timeouts_are_still_intentionally_different() -> None:
    """各家 DEFAULT_TIMEOUT 保持**故意不同**（ACP 120s vs MCP/A2A 20s）；
    如果哪个改动把它们抹平，测试就红，逼作者显式解释为什么这样是对的。"""
    from worker.runtime.agents import a2a_client, acp_client, mcp_client

    assert mcp_client.DEFAULT_TIMEOUT < acp_client.DEFAULT_TIMEOUT, (
        "ACP 与 MCP 的 DEFAULT_TIMEOUT 应保持**不同**：ACP 一次 prompt "
        "可能几十秒，MCP tool 调用秒级。合并成一个值会强行抹平协议差异。"
    )
    assert a2a_client.DEFAULT_TIMEOUT == mcp_client.DEFAULT_TIMEOUT, (
        "A2A 与 MCP 都是短任务，默认预算保持一致"
    )


def test_channel_documents_why_default_timeouts_are_not_shared() -> None:
    """channel.py 必须有显式说明"为什么这些值故意不合并"，
    否则下一轮 reviewer 会顺手把它们收进来。"""
    src = _CHANNEL.read_text(encoding="utf-8")
    assert "不**统一 DEFAULT_TIMEOUT" in src or "不统一 DEFAULT_TIMEOUT" in src, (
        "channel.py 里必须留下为什么故意保留各家 DEFAULT_TIMEOUT 的说明"
    )
