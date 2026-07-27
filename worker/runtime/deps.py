"""命令处理依赖注入容器（W3-W4 Batch 0）。

主代理统一构造并注入；各 handler 只声明所需字段，互不耦合。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worker.runtime.db.repos import Repos


@dataclass
class Deps:
    """Command Bus 注入依赖。

    Attributes:
        notify: 可选异步进度通知回调（签名
            ``async def notify(method: str, params: dict) -> None``，来自
            ``WorkerState.notify``）。测试内嵌调用未注入时为 ``None``，
            handler 侧静默跳过。
        worker_state: 反向引用 :class:`~worker.runtime.state.WorkerState`
            （``Any`` 避免循环导入）。``RestoreWorkspace`` 等替换 DB 连接的
            handler 需要同步回写 ``state.db_conn``，否则下一次 dispatch 仍
            拿到已关闭的旧连接。
    """

    repos: Repos
    ingest: Any = None
    asr: Any = None
    ai: Any = None
    tts: Any = None
    renderer: Any = None
    scene_detector: Any = None
    notify: Any = None
    worker_state: Any = None
