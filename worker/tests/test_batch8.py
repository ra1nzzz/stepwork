"""第八轮：DispatchError 迁移 + Optional → X | None 一致性护栏。

对应 yt-dev-review 归档里 P2 的两项：

1. **DispatchError 中立位落地完成**（第三轮做了 re-export 保兼容，本轮
   完成真正的迁移）：所有生产代码从 :mod:`worker.runtime.errors` 引入，
   路由层 ``commands/bus.py`` 只保留 ``from worker.runtime.errors import
   DispatchError`` 作为 re-export，43 处"handler → bus"的**反向**依赖
   归零 —— 未来想把 bus 拆薄、或者 provider 适配器想抛干净的领域错误
   而不再"顺手 import 一下 bus"，这一步是必要条件。

2. **Optional[X] 与 X | None 混用统一**（P2 一致性）：``db/repos.py``
   同文件里既写 ``Optional[str]`` 也写 ``str | None``，py3.10+ 起
   后者是官方推荐；两者混用没有功能差别，纯粹是审美漂移。
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path("worker/runtime")


# ---------------------------------------------------------------------------
# 1 DispatchError 迁移完成度
# ---------------------------------------------------------------------------


def test_no_production_code_imports_dispatch_error_from_bus() -> None:
    """生产代码里 ``from worker.runtime.commands.bus import DispatchError``
    应归零 —— 除了 bus.py 自身（re-export 兼容）与 errors.py（文档字符串
    里的反例）。这条判据锁住"路由层兼任错误词汇表"不再回来。"""
    offenders: list[str] = []
    for py in _ROOT.rglob("*.py"):
        if py.name == "errors.py":
            # 这个文件只在 docstring 里提到 bus 的旧路径，是**反例说明**
            continue
        text = py.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # bus.py 现在应该是 "from worker.runtime.errors import DispatchError"
            # 任何 "from worker.runtime.commands.bus import DispatchError"
            # 或带逗号的组合都算漏网
            if "worker.runtime.commands.bus" in line and "DispatchError" in line:
                offenders.append(f"{py}:{lineno}: {line.strip()}")
    assert offenders == [], (
        "生产代码里还有 from commands.bus import DispatchError —— 迁移没做完："
        "\n  " + "\n  ".join(offenders)
    )


def test_errors_is_the_only_definition_site() -> None:
    """``class DispatchError`` 定义**只应**在 worker/runtime/errors.py 里出现，
    bus 只做 import 不做重定义 —— 否则 re-export 与本地定义同名遮蔽，
    handler 抛的和 bus catch 的可能不是同一个类。"""
    hits: list[str] = []
    for py in _ROOT.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "DispatchError":
                hits.append(f"{py}:{node.lineno}")
    assert hits == [str(_ROOT / "errors.py") + ":25"] or len(hits) == 1, (
        f"DispatchError 又出现多份定义：{hits}"
    )


def test_bus_still_reexports_for_backward_compat() -> None:
    """即便迁移完成，bus 仍然 import DispatchError 保 re-export ——
    前端脚本 / MCP / CLI 老代码路径不能一次性破坏。"""
    src = (_ROOT / "commands/bus.py").read_text(encoding="utf-8")
    assert "from worker.runtime.errors import DispatchError" in src, (
        "bus 需要保留 DispatchError re-export 给老代码用"
    )
    from worker.runtime.commands.bus import DispatchError as FromBus
    from worker.runtime.errors import DispatchError as FromErrors

    assert FromBus is FromErrors


# ---------------------------------------------------------------------------
# 2 Optional 与 | None 一致性
# ---------------------------------------------------------------------------


def test_repos_does_not_mix_optional_and_union_syntax() -> None:
    """``db/repos.py`` 此前 ``Optional[str]`` 与 ``str | None`` 混用；
    py3.10+ 起后者是官方推荐，同文件同概念只应有一种风格。"""
    src = (_ROOT / "db/repos.py").read_text(encoding="utf-8")
    assert "Optional[" not in src, (
        "repos.py 里又出现了 Optional[X] —— 与文件内其他 `X | None` 混用"
    )
