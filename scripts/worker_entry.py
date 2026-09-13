"""PyInstaller 打包入口（W9 增补）

在打包后的 EXE 中，``__file__`` 指向 PyInstaller 临时解压目录，
``worker`` 包已被正确收集，随包资源（``migrations/`` / ``schemas/`` /
``resources/fonts/`` / ``worker/runtime/render/assets/``）由
``packaging/stepwork-worker.spec`` 的 ``datas`` 按仓库内相对位置打入。

开发模式下直接 ``python -m worker.runtime`` 即可，本脚本仅供 PyInstaller 使用。

**``--selfcheck``**：在**当前进程里**核对随包资源并把结果打成 JSON。
它存在的理由很具体 —— 「字体在 exe 里找得到吗」这种问题从外面看是**推断**：
归档里有这个文件（能翻 TOC 看到）≠ 冻结态的 ``repo_path()`` 会去读那个位置。
把探针放进冻结进程内，读的就是那台解释器真的会读的路径，推断变直读。

用法（打包后冒烟 / 排障）：

    stepwork-worker.exe --selfcheck

退出码 0 = 资源齐；1 = 有缺失（缺哪条看 JSON）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 源码模式直跑（``python scripts/worker_entry.py --selfcheck``）时 ``sys.path[0]``
# 是 ``scripts/``，仓库根不在里面 → ``import worker`` 直接失败。冻结态由
# PyInstaller 的 ``pathex`` 负责，这里加一层只在源码模式生效的引导。
if not hasattr(sys, "_MEIPASS"):
    _REPO_ROOT = str(Path(__file__).resolve().parents[1])
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

from worker.runtime.__main__ import main  # noqa: E402


def selfcheck() -> int:
    """在冻结进程内核对随包资源，JSON 打到 stdout。

    Returns:
        0 = 四类资源全部就位；1 = 有缺失。
    """
    from worker.runtime.assets import (
        BUNDLED_DIRS,
        BUNDLED_FILES,
        repo_path,
        repo_root,
    )

    root = repo_root()
    bundled: dict[str, bool] = {}
    for rel in (*BUNDLED_DIRS, *BUNDLED_FILES):
        bundled[rel] = repo_path(*rel.split("/")).exists()

    # 字体：走 ``bundled_fonts()`` 实际扫描（不是判断目录在不在）——
    # 目录在但一个字体都没扫到，正是「静默回落系统字体」那种失败。
    #
    # 判据只认**随包那份**（``resources/fonts``）。``bundled_fonts()`` 还扫一个
    # 本机字体目录（``STEPWORK_LOCAL_FONTS`` / ``~/.workbuddy/stepwork-fonts``），
    # 于是「任意字体数 > 0」在本机有本机字体时永远成立 —— 那会让这条判据在
    # 最需要它的机器上失灵。（实测：把打包目录指错后总数仍是 1，来自本机目录。）
    from worker.runtime.render.styles import bundled_fonts

    font_root = repo_path("resources", "fonts")
    fonts = bundled_fonts()
    packaged = [f for f in fonts if Path(str(f["path"])).is_relative_to(font_root)]

    from worker.runtime.bootstrap import migrations_dir

    migrations = migrations_dir()
    migration_files = sorted(migrations.glob("*.sql")) if migrations.is_dir() else []

    from worker.runtime.commands.envelope import ALLOWED_ACTOR_TYPES

    report = {
        "frozen": hasattr(sys, "_MEIPASS"),
        "repo_root": str(root),
        "bundled": bundled,
        "packaged_font_count": len(packaged),
        "packaged_font_families": sorted({str(f["family"]) for f in packaged}),
        "all_font_count": len(fonts),
        "migration_count": len(migration_files),
        "allowed_actor_types": list(ALLOWED_ACTOR_TYPES),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    ok = (
        all(bundled.values())
        and len(packaged) > 0
        and len(migration_files) > 0
        and len(ALLOWED_ACTOR_TYPES) > 0
    )
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selfcheck" in sys.argv[1:]:
        raise SystemExit(selfcheck())
    main()
