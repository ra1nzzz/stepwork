"""跨语言一致性护栏。

Python 侧与 Rust 侧对**同一份协议常量**各自定义了一份，改一处忘改另一处
就是静默漂移（dimension C P1 #5 里 review 抓到的）：

- ``MAX_FRAME_SIZE``：JSON-RPC 帧的字节上限。Python
  :data:`worker.runtime.rpc.MAX_FRAME_SIZE` 与 Rust
  ``sidecar::rpc_client::MAX_FRAME_SIZE`` 必须完全相等，否则一侧允许的大帧
  到了另一侧被硬拒 → 表现为「本地能跑、装到桌面就崩」的隐性不对称。

真·单一来源需要一次跨语言 codegen（Python 生成 → Rust include!，或
schemas/*.json 常量表 + 两侧各自 build.rs），本模块先用**一致性测试**卡住
漂移：任何一侧数值变了另一侧没跟上就红。等到 codegen 通路搭起来再换掉。
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUST_RPC_CLIENT = _REPO_ROOT / "apps/desktop/src-tauri/src/sidecar/rpc_client.rs"


def test_max_frame_size_matches_between_python_and_rust() -> None:
    """``MAX_FRAME_SIZE`` Python/Rust 数值必须完全相等 —— 单边改就是
    静默不对称（Python 发出去的大帧被 Rust 拒收、反之亦然）。"""
    from worker.runtime.rpc import MAX_FRAME_SIZE as PY_MAX

    assert _RUST_RPC_CLIENT.exists(), (
        f"Rust 侧路径没找到（可能重构了：{_RUST_RPC_CLIENT}），"
        "请同步本测试的路径常量，别让跨语言护栏悄悄失效"
    )
    src = _RUST_RPC_CLIENT.read_text(encoding="utf-8")
    m = re.search(
        r"pub\s+const\s+MAX_FRAME_SIZE\s*:\s*usize\s*=\s*([^;]+);", src
    )
    assert m is not None, "Rust 侧没找到 pub const MAX_FRAME_SIZE 定义"
    # 表达式求值：Python 侧的 ``1024 * 1024`` 与 Rust 侧一模一样（无副作用）
    rust_expr = m.group(1).strip()
    rust_val = eval(rust_expr, {"__builtins__": {}}, {})  # noqa: S307 - 只算整数表达式
    assert rust_val == PY_MAX, (
        f"MAX_FRAME_SIZE 漂移：Python={PY_MAX} vs Rust={rust_val} ({rust_expr!r})。"
        "改一侧要另一侧同步，否则一侧允许的大帧到了另一侧被硬拒 → "
        "「本地能跑、装到桌面就崩」的隐性不对称"
    )
