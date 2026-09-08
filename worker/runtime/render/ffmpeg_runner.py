"""受控 FFmpeg 子进程封装（W6，SYSTEM_SPEC §10.4 / 行 1080）。

设计原则（头脑风暴 P0）：
- FFmpeg 为**受控外部二进制**：路径来自配置/白名单或 PATH；缺失即
  ``FFmpegUnavailable``，绝不伪造渲染
- 用 ``argv list`` 调用（**不拼 shell**），禁止用户路径注入
- 取消：置位 ``cancel_event`` → ``terminate`` → 必要时 ``kill`` →
  ``wait()`` 回收，保证**取消后 0 僵尸进程**
- 进度：解析子进程 stderr 的 ``Duration:`` / ``time=`` → 比例

S1 追加（Playwright 逐帧渲染）：

- :func:`probe_duration`：读取媒体时长，供「按音频实测时长决定帧数」
- :meth:`FFmpegRunner.spawn` + :meth:`FFmpegRunner.supervise`：把一次
  ffmpeg 调用拆成「启动 → 调用方自己往 stdin 喂帧 → 收尾」，使逐帧渲染
  能**管道直连**（不落盘中间帧），同时**复用同一套取消/超时/回收语义**。
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
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


def _iter_stderr_lines(stream: Any) -> Iterator[str]:
    """逐行读取 stderr；stderr 以二进制打开时按 UTF-8 容错解码。"""
    for raw in stream:
        yield raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw


@dataclass
class _ProgressBox:
    """stderr 读取线程的进度状态（跨线程共享，用可变容器承载）。"""

    duration: float | None = None
    last: float = 0.0


def _ffprobe_candidates(
    ffprobe_bin: str | None, ffmpeg_bin: str | None
) -> list[str]:
    """候选 ffprobe 路径：显式指定 → PATH → ffmpeg 同目录（WinGet 常见布局）。"""
    out: list[str] = []
    for cand in (ffprobe_bin, shutil.which("ffprobe")):
        if cand and os.path.isfile(cand) and cand not in out:
            out.append(cand)
    if ffmpeg_bin:
        sib = str(Path(ffmpeg_bin).with_name("ffprobe" + Path(ffmpeg_bin).suffix))
        if os.path.isfile(sib) and sib not in out:
            out.append(sib)
    return out


def probe_duration(
    path: str,
    ffprobe_bin: str | None = None,
    ffmpeg_bin: str | None = None,
) -> float:
    """读取媒体时长（秒）——逐帧渲染据此决定总帧数。

    优先 ``ffprobe -show_entries format=duration``；ffprobe 找不到时退回
    ``ffmpeg -i`` 的 stderr ``Duration:`` 行（ffmpeg 无输出参数会以非零码
    退出，属预期行为）。两者皆缺失 → :class:`FFmpegUnavailable`。

    Raises:
        FFmpegUnavailable: ffprobe 与 ffmpeg 均不可用。
        FFmpegFailed: 二进制在，但读不出时长（媒体损坏或非媒体文件）。
    """
    for probe in _ffprobe_candidates(ffprobe_bin, ffmpeg_bin):
        r = subprocess.run(
            [
                probe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True,
            text=True,
            errors="replace",
        )
        if r.returncode == 0:
            try:
                return float(r.stdout.strip())
            except ValueError:
                raise FFmpegFailed(r.returncode, r.stderr[-500:]) from None
    ffmpeg = ffmpeg_bin or shutil.which("ffmpeg")
    if ffmpeg is None or not os.path.isfile(ffmpeg):
        raise FFmpegUnavailable()
    r = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", path],
        capture_output=True,
        text=True,
        errors="replace",
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        raise FFmpegFailed(r.returncode, r.stderr[-500:])
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


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

    def probe(self, path: str) -> float:
        """读取媒体时长（秒）；ffprobe 优先，退回 ffmpeg（见 :func:`probe_duration`）。"""
        return probe_duration(path, ffmpeg_bin=self.bin_path)

    def require_bin(self) -> str:
        """校验并返回 ffmpeg 可执行路径；不可用即抛 ``FFmpegUnavailable``。

        比 ``available`` 更严格：后者只表示「构造时拿到了一个非空路径」，
        本方法额外确认该路径确实是文件（``/no/such/ffmpeg`` 会在此现形）。
        """
        if not self.available:
            raise FFmpegUnavailable()
        bin_path = self.bin_path
        if bin_path is None or not os.path.isfile(bin_path):
            raise FFmpegUnavailable()
        return bin_path

    def spawn(self, args: list[str]) -> subprocess.Popen[Any]:
        """**只启动** ffmpeg 子进程，返回句柄（不等待）。

        用于「逐帧管道直连」：``stdin`` 为 PIPE，由调用方逐帧写入，随后交
        :meth:`supervise` 收尾。以**二进制模式**打开——stdin 写的是 JPEG
        裸字节，不能套 ``TextIOWrapper``。

        Raises:
            FFmpegUnavailable: 二进制缺失。
        """
        bin_path = self.require_bin()
        return subprocess.Popen(
            [bin_path, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def run(
        self,
        args: list[str],
        progress_cb: Callable[[float], None],
        cancel_event: Any,
        timeout_sec: int = 600,
    ) -> int:
        """运行 FFmpeg（启动 → 等待 → 回收）。

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
        return self.supervise(self.spawn(args), progress_cb, cancel_event, timeout_sec)

    def supervise(
        self,
        proc: subprocess.Popen[Any],
        progress_cb: Callable[[float], None],
        cancel_event: Any,
        timeout_sec: int = 600,
    ) -> int:
        """等待一个已启动的 ffmpeg 子进程收尾（取消 / 超时 / 回收）。

        与 :meth:`run` 共享完全相同的语义，只是把「启动」与「等待」拆开，
        使调用方能在两者之间往 ``stdin`` 写帧（Playwright 逐帧渲染）。

        - 进度在主线程回传（避免跨线程写 DB / SQLite 连接错线程）
        - 取消：``terminate`` → 5s 后 ``kill`` → ``wait()`` →
          :class:`FFmpegCancelled`（**取消后 0 僵尸进程**）
        - 超时：``kill`` + ``wait()`` → :class:`FFmpegFailed`
        - 非零退出：:class:`FFmpegFailed`（带 stderr 尾部 500 字符）

        Returns:
            退出码（0 为成功）。
        """
        stderr_tail: list[str] = []
        progress_q: queue.Queue[float] = queue.Queue()
        box = _ProgressBox()

        def _reader() -> None:
            stream = proc.stderr
            if stream is None:
                return
            for line in _iter_stderr_lines(stream):
                stderr_tail.append(line)
                if len(stderr_tail) > 50:
                    stderr_tail.pop(0)
                if box.duration is None and line.startswith("Duration:"):
                    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)", line)
                    if m:
                        box.duration = (
                            int(m.group(1)) * 3600
                            + int(m.group(2)) * 60
                            + int(m.group(3))
                        )
                prog = _parse_progress(line, box.duration)
                if prog is not None and prog > box.last:
                    box.last = prog
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
        return int(proc.returncode or 0)
