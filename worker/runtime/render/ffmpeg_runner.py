"""受控 FFmpeg 子进程封装（W6，SYSTEM_SPEC §10.4 / 行 1080）。

设计原则（头脑风暴 P0）：
- FFmpeg 为**受控外部二进制**：路径来自配置/白名单或 PATH；缺失即
  ``FFmpegUnavailable``，绝不伪造渲染
- 用 ``argv list`` 调用（**不拼 shell**），禁止用户路径注入
- 取消：置位 ``cancel_event`` → ``terminate`` → 必要时 ``kill`` →
  ``wait()`` 回收，保证**取消后 0 僵尸进程**
- 进度：解析子进程 stderr 的 ``Duration:`` / ``time=`` → 比例
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any


class FFmpegUnavailable(Exception):
    """FFmpeg 二进制不可用（缺失或未配置）。"""


class FFmpegCancelled(Exception):
    """渲染因取消事件被终止。"""


class FFmpegFailed(Exception):
    """FFmpeg 以非零码退出。"""

    def __init__(self, code: int, tail: str) -> None:
        self.code = code
        self.tail = tail
        super().__init__(f"ffmpeg exit {code}")


def _parse_progress(line: str, duration_sec: float | None) -> float | None:
    """从一行 stderr 解析进度比例（0.0–1.0）。"""
    if duration_sec is None or duration_sec <= 0:
        return None
    m = re.search(r"time=(\d+):(\d+):(\d+)", line)
    if not m:
        return None
    cur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return max(0.0, min(1.0, cur / duration_sec))


class FFmpegRunner:
    """封装一次 FFmpeg 调用。"""

    def __init__(self, bin_path: str | None = None) -> None:
        resolved = shutil.which("ffmpeg") if bin_path is None else bin_path
        self.bin_path: str | None = resolved
        self.available = self.bin_path is not None

    def run(
        self,
        args: list[str],
        progress_cb: Callable[[float], None],
        cancel_event: Any,
        timeout_sec: int = 600,
    ) -> int:
        """运行 FFmpeg。

        Args:
            args: FFmpeg 参数（不含可执行名）。
            progress_cb: 进度回调（0.0–1.0）。
            cancel_event: ``threading.Event``；置位即终止。
            timeout_sec: 最大运行秒数。

        Returns:
            退出码（0 为成功）。

        Raises:
            FFmpegUnavailable: 二进制缺失。
            FFmpegCancelled: 被取消事件终止。
            FFmpegFailed: 非零退出。
        """
        if not self.available:
            raise FFmpegUnavailable()
        bin_path = self.bin_path
        if bin_path is None or not os.path.isfile(bin_path):
            raise FFmpegUnavailable()
        proc = subprocess.Popen(
            [bin_path, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        duration_sec: float | None = None
        last_progress = 0.0
        stderr_tail: list[str] = []
        progress_q: queue.Queue[float] = queue.Queue()

        def _reader() -> None:
            nonlocal duration_sec, last_progress
            stream = proc.stderr
            if stream is None:
                return
            for line in stream:
                stderr_tail.append(line)
                if len(stderr_tail) > 50:
                    stderr_tail.pop(0)
                if line.startswith("Duration:"):
                    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)", line)
                    if m:
                        duration_sec = (
                            int(m.group(1)) * 3600
                            + int(m.group(2)) * 60
                            + int(m.group(3))
                        )
                prog = _parse_progress(line, duration_sec)
                if prog is not None and prog > last_progress:
                    last_progress = prog
                    progress_q.put(prog)

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        try:
            waited = 0.0
            while proc.poll() is None:
                # 进度回传在主线程（避免跨线程写 DB / SQLite 连接错线程）
                while not progress_q.empty():
                    progress_cb(progress_q.get_nowait())
                if cancel_event is not None and cancel_event.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    proc.wait()  # 回收 → 0 僵尸
                    raise FFmpegCancelled()
                if waited >= timeout_sec:
                    proc.kill()
                    proc.wait()
                    raise FFmpegFailed(-1, "".join(stderr_tail)[-500:])
                time.sleep(0.05)
                waited += 0.05
            # 进程退出后排空剩余进度（仍在主线程）
            while not progress_q.empty():
                progress_cb(progress_q.get_nowait())
        finally:
            t.join(timeout=1)
        if proc.returncode != 0:
            raise FFmpegFailed(proc.returncode or -1, "".join(stderr_tail)[-500:])
        return proc.returncode
