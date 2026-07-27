"""PyInstaller 打包入口（W9 增补）

在打包后的 EXE 中，``__file__`` 指向 PyInstaller 临时解压目录，
``worker`` 包已被正确收集，``migrations`` 目录通过 ``--add-data`` 打入。

开发模式下直接 ``python -m worker.runtime`` 即可，本脚本仅供 PyInstaller 使用。
"""

from __future__ import annotations

from worker.runtime.__main__ import main

if __name__ == "__main__":
    main()
