"""FFmpegRunner 测试（W6）：可用/进度、取消后回收、缺失即不可用。"""

from __future__ import annotations

import os
import sys
import tempfile
import threading

from worker.runtime.render.ffmpeg_runner import (
    FFmpegCancelled,
    FFmpegRunner,
    FFmpegUnavailable,
)

PY = sys.executable
FAKE = os.path.join(os.path.dirname(__file__), "fakes", "fake_ffmpeg.py")


def test_run_ok_and_progress() -> None:
    out = os.path.join(tempfile.gettempdir(), "ff_out.mp4")
    if os.path.exists(out):
        os.remove(out)
    progresses: list[float] = []
    runner = FFmpegRunner(bin_path=PY)
    code = runner.run([FAKE, out], progresses.append, None)
    assert code == 0
    assert os.path.exists(out)
    assert progresses and max(progresses) > 0.0


def test_unavailable() -> None:
    runner = FFmpegRunner(bin_path="/no/such/ffmpeg")
    try:
        runner.run(["x"], lambda p: None, None)
        raise AssertionError("expected FFmpegUnavailable")
    except FFmpegUnavailable:
        pass


def test_cancel_no_zombie() -> None:
    out = os.path.join(tempfile.gettempdir(), "ff_sleep.mp4")
    runner = FFmpegRunner(bin_path=PY)
    event = threading.Event()
    raised = False

    def _runner() -> None:
        nonlocal raised
        try:
            runner.run([FAKE, "--sleep", out], lambda p: None, event)
        except FFmpegCancelled:
            raised = True

    th = threading.Thread(target=_runner)
    th.start()
    threading.Event().wait(1.0)
    event.set()
    th.join(timeout=10)
    assert raised is True
    assert not th.is_alive()
    # sleep 分支不触碰输出 → 侧面印证子进程被提前终止
    assert not os.path.exists(out)
