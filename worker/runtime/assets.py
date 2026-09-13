"""随包资源定位：**源码运行 vs 冻结 exe（PyInstaller）**的唯一分岔点。

侧车打包成单文件 exe 后，PyInstaller 会把内容解包到一个临时目录并把
``sys._MEIPASS`` 指向它。此时 ``Path(__file__).resolve().parents[N]`` 全部
落在**解包目录里的相对位置**，而不是仓库根 —— 仓库根那条会指向一个不存在的
位置，于是资源**静默找不到**：

- ``migrations/*.sql`` 找不到 → 库建不起来 → 侧车启动即死（无兜底）
- ``schemas/command-envelope.schema.json`` 找不到 → ``actor.type`` 白名单退回硬编码兜底
- ``resources/fonts`` 找不到 → 渲染悄悄退回系统字体（画面对，字形不对）
- ``worker/runtime/render/assets/s1_probe.html`` 找不到 → 探路文档兜底失效

所以调用方一律写 ``repo_path("migrations")`` 这种**仓库根相对路径**，而打包配置
（``packaging/stepwork-worker.spec``）的 ``datas`` 直接由本模块的
:func:`bundled_datas` 生成 —— 两边同源，不靠「记得同步」。
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath


def repo_root() -> Path:
    """仓库根目录；冻结（PyInstaller）时是解包根 ``sys._MEIPASS``。

    每次调用都重新判断，因此「导入后才知道自己在不在 exe 里」也成立，
    测试也可以直接改 ``sys._MEIPASS`` 来验证分流。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        return Path(meipass)
    # worker/runtime/assets.py → parents[2] 即仓库根
    return Path(__file__).resolve().parents[2]


def repo_path(*parts: str) -> Path:
    """仓库根下的资源路径（例：``repo_path("resources", "fonts")``）。"""
    return repo_root().joinpath(*parts)


# ---------------------------------------------------------------------------
# 随包资源清单 —— **打包配置与测试的唯一事实源**
#
# ``packaging/stepwork-worker.spec`` 的 ``datas`` 与守卫测试都从这里取列表，
# 于是「代码会去读的东西」和「exe 里真打进去的东西」不可能各走各的 —— 此前
# 两处各写一份时，漏打是**静默**的：migrations 丢了侧车起不来（唯一会叫的
# 那一个），字体丢了只是字形悄悄变掉，没人会发现。
#
# 新增资源读取点 = 加一行到下面 + 加一行到
# ``worker/tests/test_assets.py::test_resource_call_sites_hang_off_repo_root``，
# 只做一半会被测试点名。
# ---------------------------------------------------------------------------

#: 整目录随包（仓库根相对路径，POSIX 分隔符）。
BUNDLED_DIRS: tuple[str, ...] = (
    "migrations",  # 建库 SQL：丢了 bootstrap_db 直接抛，侧车启动即死
    "resources/fonts",  # 渲染字体：丢了静默回落系统字体
    "worker/runtime/render/assets",  # s1_probe.html：渲染器默认文档
)

#: 单个文件随包（收进它在 ``_MEIPASS`` 里的父目录）。
BUNDLED_FILES: tuple[str, ...] = (
    "schemas/command-envelope.schema.json",  # actor.type 白名单的事实源
)


def bundled_datas(repo: Path | None = None) -> list[tuple[str, str]]:
    """PyInstaller ``datas`` 条目：``(源路径, _MEIPASS 内相对目录)``。

    ``dest`` 只到**目录**一级（PyInstaller 的语义如此），所以 ``BUNDLED_FILES``
    里的文件收进它的父目录。``repo`` 缺省取 :func:`repo_root`；测试传临时目录
    即可验证形状，不必真跑一次 PyInstaller。
    """
    root = repo if repo is not None else repo_root()
    datas = [(str(root / d), d) for d in BUNDLED_DIRS]
    datas += [(str(root / f), str(PurePosixPath(f).parent)) for f in BUNDLED_FILES]
    return datas
