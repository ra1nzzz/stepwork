"""命令处理依赖注入容器（W3-W4 Batch 0）。

主代理统一构造并注入；各 handler 只声明所需字段，互不耦合。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from worker.runtime.db.repos import Repos


@dataclass
class Deps:
    """Command Bus 注入依赖。"""

    repos: Repos
    ingest: Any = None
    asr: Any = None
    ai: Any = None
    tts: Any = None
    renderer: Any = None
