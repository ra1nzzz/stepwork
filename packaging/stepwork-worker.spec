# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：worker 侧车（Tauri ``bundle.externalBin`` 的目标）。

**不要直接对它敲 ``pyinstaller``** —— 用 ``scripts/build_worker_sidecar.ps1``，
构建脚本负责 Python 发现、产物按 target triple 改名与投递到
``apps/desktop/src-tauri/binaries/``。

侧车从「源码运行」变成「单文件 exe」只有两个真实的差异点，都收敛在这里：

1. **``datas`` 从 ``worker.runtime.assets.bundled_datas()`` 生成**，与运行期
   ``repo_path()`` 的调用点同源。漏打的代价是不对称的：``migrations`` 丢了
   侧车直接起不来（会叫），``resources/fonts`` 丢了只是字形悄悄变回系统字体
   （不会叫）—— 所以这里不能靠人工核对清单。
2. **``hiddenimports`` 必须显式收集 ``worker`` 的每个子模块**。``bus.py`` 用
   ``importlib.import_module(module_path)`` 路由，模块名来自 ``_ROUTES`` 这张
   **字典**而不是 import 语句，静态分析看不到 —— 99 个命令对应的 33 个 handler
   模块一个都不能少。于是用 ``collect_submodules("worker")`` 整包收，而不是
   手抄 33 行（手抄的清单迟早跟 ``_ROUTES`` 漂开）。

可选引擎默认**不**打包，用环境变量单独开（见 README 的「侧车打包」一节）：

===============================  ==============================================
``STEPWORK_BUNDLE_RENDER=1``     连 ``playwright`` 一起打（体积 +100MB 量级；
                                 浏览器二进制 pip 装不了，仍需另跑
                                 ``playwright install chromium``）
``STEPWORK_BUNDLE_ASR=1``        连 ``faster_whisper``（连带 ctranslate2 等）
``STEPWORK_BUNDLE_TTS=1``        连 ``edge_tts``
===============================  ==============================================

关掉引擎不是「功能缺失」而是**显式能力边界**：``resolve.py`` 的
``_has_module()`` 守卫会让对应 provider 返回 ``None`` → handler 转
``UNAVAILABLE``。也就是说默认 exe 里 ``STEPWORK_RENDER_PROVIDER=playwright``
会**报不可用**，而不是静默换 ffmpeg 渲出另一条片子。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH 由 PyInstaller 注入 = 本文件所在目录（<repo>/packaging）
REPO_ROOT = Path(SPECPATH).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from worker.runtime.assets import bundled_datas  # noqa: E402

_ENTRY = REPO_ROOT / "scripts" / "worker_entry.py"


def _flag(name: str) -> bool:
    """环境变量开关：``1`` / ``true`` / ``yes`` / ``on`` 为真。"""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


# 可选引擎（默认关）：开一个就把对应的 excludes 撤掉
_BUNDLE_RENDER = _flag("STEPWORK_BUNDLE_RENDER")
_BUNDLE_ASR = _flag("STEPWORK_BUNDLE_ASR")
_BUNDLE_TTS = _flag("STEPWORK_BUNDLE_TTS")

#: 明确不随包的东西。``tkinter`` 是 PyInstaller 的经典误收项（本仓无 GUI 依赖）；
#: 三个可选引擎不开关就排除 —— 它们的 import 都在**函数体**里，静态分析照样
#: 会跟着收进来，只能靠 excludes 拦。
excludes = ["tkinter"]
if not _BUNDLE_RENDER:
    excludes.append("playwright")
if not _BUNDLE_ASR:
    excludes += ["faster_whisper", "ctranslate2", "onnxruntime", "tokenizers"]
if not _BUNDLE_TTS:
    excludes.append("edge_tts")

a = Analysis(  # noqa: F821  (Analysis 由 PyInstaller 注入)
    [str(_ENTRY)],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=bundled_datas(REPO_ROOT),
    hiddenimports=collect_submodules("worker"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="stepwork-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # upx 关闭：本仓不发 upx，且压缩大字体收益有限、反而拖慢每次启动的解包
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # 侧车走 stdin/stdout 长度前缀 JSON-RPC：**必须**保留控制台
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

print(f"[stepwork-worker.spec] datas={len(bundled_datas(REPO_ROOT))} 条 "
      f"hiddenimports={len(collect_submodules('worker'))} 个 "
      f"excludes={excludes}")
