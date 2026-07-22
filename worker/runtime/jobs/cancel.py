"""RenderJob 取消注册表（W6，SYSTEM_SPEC §10.4）。

渲染在同步 handler 内运行；UI 取消通过 ``CancelJob`` 命令置位
对应 ``threading.Event``，由 :mod:`worker.runtime.render.ffmpeg_runner`
终止 FFmpeg 子进程。
"""

from __future__ import annotations

import threading

CANCEL_REGISTRY: dict[str, threading.Event] = {}


def register(job_id: str, event: threading.Event) -> None:
    """注册某任务的取消事件。"""
    CANCEL_REGISTRY[job_id] = event


def request(job_id: str) -> bool:
    """置位某任务的取消事件。返回是否找到该任务。"""
    ev = CANCEL_REGISTRY.get(job_id)
    if ev is None:
        return False
    ev.set()
    return True


def clear(job_id: str) -> None:
    """清除某任务的取消事件。"""
    CANCEL_REGISTRY.pop(job_id, None)
