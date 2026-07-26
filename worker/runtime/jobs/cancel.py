"""任务取消注册表（W6 渲染 + UX §10.2 全任务类型）。

两类任务、两种取消机制：

- **渲染**（同步跑在 worker 线程里，`asyncio` 无法打断）：注册
  ``threading.Event``，:mod:`worker.runtime.render.ffmpeg_runner` 轮询后
  终止 FFmpeg 子进程。
- **异步任务**（转写 / 分析 / 角度 / 脚本，工作是 ``await`` 的 provider
  调用）：注册其 ``asyncio.Task``，取消时 ``task.cancel()``，在下一个
  await 点（含正在进行的网络调用）真正中断。

此前只有渲染注册，因此 UI 上对转写/分析点「取消」后端照常跑完并落库，
与「进度可见 / 长任务必须可取消」相悖。
"""

from __future__ import annotations

import asyncio
import threading

CANCEL_REGISTRY: dict[str, threading.Event] = {}
"""同步任务（渲染）的取消事件表。"""

TASK_REGISTRY: dict[str, asyncio.Task[object]] = {}
"""异步任务的 Task 表（取消即 ``task.cancel()``）。"""


def register(job_id: str, event: threading.Event) -> None:
    """注册某同步任务的取消事件。"""
    CANCEL_REGISTRY[job_id] = event


def register_task(job_id: str, task: asyncio.Task[object]) -> None:
    """注册某异步任务的 Task（供 ``CancelJob`` 真正打断）。"""
    TASK_REGISTRY[job_id] = task


def request(job_id: str) -> bool:
    """请求取消某任务。返回是否找到该任务（两类注册表都查）。"""
    found = False
    ev = CANCEL_REGISTRY.get(job_id)
    if ev is not None:
        ev.set()
        found = True
    task = TASK_REGISTRY.get(job_id)
    if task is not None:
        # 已完成的 task 再 cancel 无副作用，但不算「拦下了」
        if not task.done():
            task.cancel()
            found = True
    return found


def clear(job_id: str) -> None:
    """清除某任务的取消登记（两类注册表都清）。"""
    CANCEL_REGISTRY.pop(job_id, None)
    TASK_REGISTRY.pop(job_id, None)
