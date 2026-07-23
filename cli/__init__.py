"""STEPWORK 命令行入口包（W7 Phase 3）。

通过 Command Bus 与 worker 后端交互：构造信封（source="cli"，
actor.type="desktop"）→ ``run_command`` → 美化打印结果 JSON。
"""

from __future__ import annotations

__all__: list[str] = ["main", "build_parser"]
