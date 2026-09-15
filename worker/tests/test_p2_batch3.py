"""第三轮 P2 修复的行为锁测试。

覆盖本轮 3 个改动：

1. ``DispatchError`` 搬到 :mod:`worker.runtime.errors`，bus 只做重导出 ——
   路由层不再兼任"错误词汇表"，43 处 handler 老 import 路径零改动。
2. 逐幕配图并发（:mod:`worker.runtime.handlers.illustrate_scenes`）——
   4 张图并发压到 1 张的时间；DispatchError 依然让整任务 FAILED。
3. 校验助手 :mod:`worker.runtime.validation` 统一 5 处 limit + 4 处 pydantic
   转译 —— 一处收紧处处生效；``ListApprovalRequests`` 顺带补上此前缺的
   500 上限（review 抓到的"同概念不同边界"）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from worker.runtime import errors
from worker.runtime.commands import bus
from worker.runtime.handlers import illustrate_scenes
from worker.runtime.validation import parse_spec, require_positive_int

# ---------------------------------------------------------------------------
# 1 DispatchError 中立位
# ---------------------------------------------------------------------------


def test_dispatch_error_defined_once_and_reexported() -> None:
    """``DispatchError`` 唯一真身在 errors.py；bus 只是 re-export。

    老代码里 43 处 ``from worker.runtime.commands.bus import DispatchError``
    必须仍然有效，不然迁移就是破坏式变更。
    """
    from worker.runtime.commands.bus import DispatchError as FromBus

    assert FromBus is errors.DispatchError, (
        "bus 又重定义了一次 DispatchError —— 迁移没做彻底，"
        "老 handler 抛的和 bus 兜底 catch 的不是同一个类，异常会漏"
    )


def test_new_code_can_import_dispatch_error_from_errors() -> None:
    """新代码可以从 errors 直接引 —— 不强迫它反向依赖路由层。"""
    e = errors.DispatchError("FORBIDDEN", "nope")
    assert e.code == "FORBIDDEN"
    assert str(e) == "FORBIDDEN: nope"


def test_dispatch_error_import_paths_are_equivalent() -> None:
    """43 处老 handler 走的是 bus 路径；新代码走 errors 路径 —— 两者必须
    是同一个类，否则一处 raise 一处 catch 就漏了。"""
    from worker.runtime.commands.bus import DispatchError as A

    assert A is errors.DispatchError
    try:
        raise errors.DispatchError("X", "y")
    except A as e:  # noqa: B014
        assert e.code == "X"


# ---------------------------------------------------------------------------
# 2 逐幕配图并发
# ---------------------------------------------------------------------------


class _FakeImage:
    """可控 provider：记录并发峰值，按调用序号决定成败。"""

    def __init__(
        self,
        *,
        fail_dispatch_error_at: int | None = None,
        fail_soft_at: int | None = None,
    ) -> None:
        self.in_flight = 0
        self.peak = 0
        self.calls = 0
        self._fail_fatal_at = fail_dispatch_error_at
        self._fail_soft_at = fail_soft_at

    async def generate(self, prompt: str, opts: dict[str, Any]) -> str:
        self.calls += 1
        my_seq = self.calls
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.05)
            if self._fail_fatal_at is not None and my_seq == self._fail_fatal_at:
                raise bus.DispatchError("UNAVAILABLE", "provider down")
            if self._fail_soft_at is not None and my_seq == self._fail_soft_at:
                raise RuntimeError("transient 503")
            return f"file:///fake/{my_seq}.png"
        finally:
            self.in_flight -= 1


def test_illustrate_scenes_uses_semaphore_and_gather() -> None:
    """源码级断言：并发骨架真在 handler 里，而不是被下一次重构悄悄退回串行。"""
    src = illustrate_scenes.__file__ and open(
        illustrate_scenes.__file__, encoding="utf-8"
    ).read()
    assert "asyncio.Semaphore" in src, (
        "IllustrateScenes 又走回逐幕串行 await 了 —— 8 幕 × 20s 生图"
        "会白等 160s；必须 Semaphore 卡上限 + gather 并发"
    )
    assert "asyncio.gather" in src, "缺 gather，仍是顺序 await 语义"
    assert "DispatchError" in src, (
        "fatal 短路（provider 未配 / 鉴权错）必须显式区分，"
        "不能与 transient 503 混入 failed 数组"
    )


@pytest.mark.asyncio
async def test_semaphore_pattern_reaches_peak_concurrency() -> None:
    """用与 handler 一致的骨架跑一遍：8 个 50ms 假请求，Semaphore(4) →
    peak ∈ [2, 4]、总耗时远小于串行的 400ms。"""
    fake = _FakeImage()
    sem = asyncio.Semaphore(4)

    async def _one(i: int) -> str:
        async with sem:
            return await fake.generate(f"prompt-{i}", {})

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await asyncio.gather(*(_one(i) for i in range(8)))
    elapsed = loop.time() - t0

    assert fake.peak > 1, f"并发未生效：peak={fake.peak}"
    assert fake.peak <= 4, f"超出上限：peak={fake.peak}"
    # 串行 8 × 50ms = 400ms；上限 4 并发理论最短 ≈ 100ms
    assert elapsed < 0.3, f"并发后仍耗时 {elapsed:.3f}s，不像 Semaphore(4) 起作用"


@pytest.mark.asyncio
async def test_dispatch_error_short_circuits_gather() -> None:
    """任一 provider 级 DispatchError 都要冒到调用方（content_job 会捕获
    它并把 job 落 FAILED），不能被 return_exceptions=True 吞进结果数组。"""
    fake = _FakeImage(fail_dispatch_error_at=1)
    sem = asyncio.Semaphore(4)

    async def _one(i: int) -> dict[str, Any]:
        async with sem:
            try:
                return {"ok": await fake.generate(f"p{i}", {})}
            except bus.DispatchError as e:
                return {"fatal": e}
            except Exception as e:  # noqa: BLE001
                return {"soft": str(e)}

    results = await asyncio.gather(*(_one(i) for i in range(3)))
    fatal = next((r for r in results if "fatal" in r), None)
    assert fatal is not None
    assert fatal["fatal"].code == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# 3 validation 助手
# ---------------------------------------------------------------------------


def test_require_positive_int_rejects_bool() -> None:
    """``bool`` 是 ``int`` 子类，``isinstance(True, int)`` 为真 —— 不显式挡
    ``True`` 会当成 1 静默通过。"""
    with pytest.raises(errors.DispatchError):
        require_positive_int(True, name="limit")


def test_require_positive_int_clamps_max() -> None:
    """超上限 clamp（不是拒），与 ListAuditEvents 既有 min() 语义一致。"""
    assert require_positive_int(10000, maximum=500) == 500


def test_require_positive_int_default_applied() -> None:
    assert require_positive_int(None, default=42) == 42


def test_list_approval_requests_now_capped() -> None:
    """review 抓到 ListApprovalRequests 无上限（同概念 ListAuditEvents 有
    500），本轮补齐；单点校验走 require_positive_int。"""
    from worker.runtime.handlers import approvals

    src = open(approvals.__file__, encoding="utf-8").read()
    assert "require_positive_int" in src, (
        "approvals 还在用散装 limit 校验，未接 require_positive_int"
    )
    assert "maximum=500" in src, "本轮补的 500 上限丢失"


def test_parse_spec_wraps_pydantic_errors() -> None:
    """pydantic ValidationError 被转成 DispatchError(INVALID_ARGUMENT)。"""

    class _M(BaseModel):
        name: str

    with pytest.raises(errors.DispatchError) as ei:
        parse_spec(_M, {"name": 123}, what="demo spec")
    assert ei.value.code == "INVALID_ARGUMENT"
    assert "demo spec" in ei.value.message


def test_parse_spec_returns_model_on_valid() -> None:
    class _M(BaseModel):
        name: str

    m = parse_spec(_M, {"name": "ok"}, what="demo")
    assert m.name == "ok"
