"""``runtime.console_hint()`` 的守卫测试。

背景：侧车是 stdio JSON-RPC 进程，手动在终端里跑起来只会看到 ``runtime.ready``
加每 5 秒一条心跳 —— 协议上它**永远等不到帧**，但看上去像卡住了。于是
``amain()`` 在 stdin 是终端时往 stderr 写一句解释。

两条红线各有一条测试钉住：

1. **只在终端场景出现** —— 管道 / 重定向 / 测试替身都不许插话（父进程 Tauri
   走的就是管道，那儿多写一行 stderr 纯属噪音）。Windows 上光看 ``isatty()``
   还不够：``NUL`` 是字符设备，``isatty()`` 对它返回 ``True``，于是
   ``subprocess.DEVNULL`` / ``Stdio::null()`` 会被误判成「人在终端前」——
   探针另用 ``GetConsoleMode`` 收窄（见 ``_is_console_handle``）。
2. **提示绝不能落进 stdout** —— stdout 是帧协议通道，混进任何非帧字节都会
   让父进程的解析器读到半截（而帧是长度前缀的，错位之后无法自愈）。
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from worker.runtime.__main__ import _STDIO_HINT, console_hint


def _decode_frames(data: bytes) -> list[dict[str, Any]]:
    """按帧协议把 stdout 字节流解回来 —— 任何非帧字节都会在这里炸。

    比「文本里不含某句话」强得多：帧是**长度前缀**的，任意一个多余字节都会让
    后续帧全部错位，而父进程无法自愈。所以判据是「能不能完整解回来」，不是
    「有没有出现某个词」。
    """
    frames: list[dict[str, Any]] = []
    offset = 0
    while offset < len(data):
        assert len(data) - offset >= 4, f"尾部残帧（不足 4 字节头）：{data[offset:]!r}"
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        body = data[offset + 4 : offset + 4 + length]
        assert len(body) == length, f"帧被截断：头声明 {length} 字节，实得 {len(body)}"
        frames.append(json.loads(body.decode("utf-8")))
        offset += 4 + length
    return frames


class _FakeStdout:
    """既能被 ``print`` 写、又有 ``.buffer`` 的 stdout 替身。

    必须两者兼备：只给 ``SimpleNamespace(buffer=...)`` 的话，一旦真有人往
    stdout 写文本就会 ``AttributeError`` —— 那会让「stdout 干净吗」这条判据
    变成「harness 会不会崩」，而不是真的在检查污染。
    """

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, text: str) -> int:
        self.buffer.write(text.encode("utf-8"))
        return len(text)

    def flush(self) -> None:
        pass


class _Stream:
    """最小流替身：只实现被探测的那一个方法。"""

    def __init__(self, interactive: bool) -> None:
        self._interactive = interactive

    def isatty(self) -> bool:
        return self._interactive


def test_console_hint_fires_on_a_terminal() -> None:
    """人手终端 → 出提示，且提示里真的给了两条可执行的出路。"""
    hint = console_hint(_Stream(interactive=True))
    assert hint is not None
    assert hint == _STDIO_HINT
    # 文案不能只说「这不是 CLI」而不给出路 —— 那样等于把困惑原样还回去
    assert "--selfcheck" in hint
    assert "test_sidecar.py" in hint


def test_console_hint_is_silent_on_a_pipe() -> None:
    """管道（= Tauri 真正启动它的方式）→ 一个字都不写。"""
    assert console_hint(_Stream(interactive=False)) is None


@pytest.mark.skipif(
    sys.platform != "win32", reason="NUL 设备的 isatty 怪癖只在 Windows 上"
)
def test_console_hint_is_silent_when_stdin_is_the_null_device() -> None:
    """``< NUL`` / ``subprocess.DEVNULL`` / Rust ``Stdio::null()`` → 不插话。

    Windows 的 ``NUL`` 是**字符设备**，而 CRT 的 ``_isatty`` 只问「是不是字符
    设备」→ 返回 ``True``。只靠 ``isatty()`` 就把「没人看的空重定向」当成
    「人在终端前」，替管道式父进程（CI / 脚本 / ``Stdio::null()``）刷一段没用的
    stderr。实测 3.12.4：``subprocess.DEVNULL`` → ``True``、``subprocess.PIPE``
    → ``False`` —— 后者才是 Tauri 的形态，所以主链路本来就对，这里收窄的是假阳性。

    故意用**真的 NUL 句柄**而不是替身：要测的正是真句柄上 ``GetConsoleMode``
    会不会失败。上面那条 ``isatty() is True`` 是**前提断言** —— 它保证这条测试
    不会因为「``isatty`` 碰巧变 False」而变成空转。
    """
    with open("NUL") as nul:
        assert nul.isatty() is True
        assert console_hint(nul) is None


def test_console_hint_tolerates_streams_without_isatty() -> None:
    """没有 ``isatty`` 的替身不许抛异常。

    ``test_main_concurrency.py`` 用 ``SimpleNamespace(buffer=...)`` 伪装 stdin，
    真去 ``.isatty()`` 会 ``AttributeError`` —— 探针崩掉会把整条启动路径带下去。
    """
    assert console_hint(SimpleNamespace(buffer=io.BytesIO())) is None
    # 实现了 isatty 但抛 ValueError（已关闭的流）同样不许炸
    assert console_hint(_RaisingStream()) is None


class _RaisingStream:
    """模拟已关闭的流：``isatty()`` 抛 ``ValueError``。"""

    def isatty(self) -> bool:
        raise ValueError("I/O operation on closed file")


async def test_hint_goes_to_stderr_and_leaves_stdout_clean(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """端到端：终端场景下提示只出现在 stderr，stdout 仍是纯帧。"""
    from worker.runtime.__main__ import amain

    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))

    class _TtyStdin:
        """真终端形状：``isatty()`` 在**文本流**上，帧从 ``.buffer`` 读。

        （第一版把 isatty 挂在 ``.buffer`` 上，于是探针看不见 —— 真实 CPython 里
        ``sys.stdin.isatty()`` 会委托给底层 buffer，所以探文本流是对的。）
        """

        def __init__(self) -> None:
            self.buffer = io.BytesIO()

        def isatty(self) -> bool:
            return True

    stdout = _FakeStdout()
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    monkeypatch.setattr(sys, "stdout", stdout)

    exit_code = await asyncio.wait_for(amain(), timeout=10.0)
    assert exit_code == 0

    # stdout 必须**严格**是帧的串联（stdin 随即 EOF，故只有 ready 一条）
    frames = _decode_frames(stdout.buffer.getvalue())
    assert [f.get("method") for f in frames] == ["runtime.ready"]

    # 提示在 stderr，不出现在 stdout
    assert "--selfcheck" in capsys.readouterr().err
    assert _STDIO_HINT.encode("utf-8") not in stdout.buffer.getvalue()
