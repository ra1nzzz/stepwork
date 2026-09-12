"""发布 Provider 可用性探测测试（S7 第一段：接口先于实现）。

锁死四件事：

1. **三态只有三个**（``ready`` / ``unavailable`` / ``need_login``）——
   多出来的中间态正是静默降级藏身的地方；
2. **退出码 → 三态的映射**，尤其 ``66``（结果为空）算 ``READY``：把它当
   不可用会让人去修一个没坏的桥；
3. **协议里没有 ``publish``**（ADR-008 的结构性保证）—— 这条断言是
   **变更检测器**，不是现状描述：有人日后给协议加动作时它当场红；
4. **真 subprocess 的纪律**：能拿到退出码与输出、超时**强杀不挂起**、
   没装时走返回值而不是异常。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.providers.publish.base import (
    EX_CONFIG,
    EX_NOINPUT,
    EX_NOPERM,
    EX_OK,
    EX_TEMPFAIL,
    EX_UNAVAILABLE,
    Availability,
    AvailabilityState,
    PublishProvider,
    classify_exit,
)
from worker.runtime.providers.publish.opencli import OpenCliPublishProvider
from worker.runtime.providers.resolve import resolve_publish_provider
from worker.runtime.results.models import ProbePublishProviderDetail

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"
_ENV_KEY = "STEPWORK_PUBLISH_PROVIDER"
#: 必定不在 PATH 上的可执行名（用 ``.invalid`` 前缀避免和真工具撞名）
_ABSENT_BIN = "stepwork-no-such-opencli.invalid"


def _deps(**overrides: Any) -> Deps:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    return Deps(repos=Repos(conn), **overrides)


def _env(command_type: str = "ProbePublishProvider") -> dict[str, Any]:
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": "ws-pub",
        "projectId": None,
        "payload": {},
        "requestedAt": datetime.now(UTC).isoformat(),
    }


class _StubProvider:
    """回预置结果的 Provider 替身。

    存在的意义是让 ``READY`` / ``NEED_LOGIN`` 两条路径**在没装 opencli 的
    机器上也能被测到** —— 否则这两条分支只有在装了外部工具的环境里才走过，
    等于长期无人看守。
    """

    name = "stub"

    def __init__(self, state: AvailabilityState) -> None:
        self._state = state

    async def probe(self) -> Availability:
        return Availability(
            state=self._state,
            provider=self.name,
            detail=f"stub 说 {self._state.value}",
            hint="stub 的提示",
            exit_code=EX_OK if self._state is AvailabilityState.READY else None,
        )


# ---------------------------------------------------------------------------
# 1. 三态与退出码映射
# ---------------------------------------------------------------------------


def test_classify_exit_maps_sysexits_to_three_states() -> None:
    """``sysexits.h`` → 三态。``66`` 是这套映射里唯一反直觉的一条。"""
    assert classify_exit(EX_OK) is AvailabilityState.READY
    # 66 = 查得到但结果为空：桥是通的，只是这次没东西可报
    assert classify_exit(EX_NOINPUT) is AvailabilityState.READY
    # 77 = 权限不足：装好了、桥也通，只差登录 —— 用户做一步就能好
    assert classify_exit(EX_NOPERM) is AvailabilityState.NEED_LOGIN
    for code in (EX_UNAVAILABLE, EX_TEMPFAIL, EX_CONFIG, 1, 2, 127):
        assert classify_exit(code) is AvailabilityState.UNAVAILABLE, code


def test_classify_exit_treats_missing_code_as_unavailable() -> None:
    """拿不到退出码（超时被杀）→ 不可用，绝不假装能填。"""
    assert classify_exit(None) is AvailabilityState.UNAVAILABLE


def test_states_are_exactly_three() -> None:
    """三态就是三种。多一个中间态，就一定有人把它当「能用」。"""
    assert {s.value for s in AvailabilityState} == {
        "ready",
        "unavailable",
        "need_login",
    }


# ---------------------------------------------------------------------------
# 2. ADR-008 的结构性保证
# ---------------------------------------------------------------------------


def test_protocol_exposes_only_name_and_probe() -> None:
    """ADR-008：协议里**没有** ``publish``，这一轮也刻意没有 ``fill``。

    这条断言是「变更检测器」—— 有人日后给协议加动作时它当场红，逼那次改动
    去走 ADR，而不是悄悄绕开 V0.x 的边界（V0.x 只允许 FILL_AND_PREVIEW）。

    ``fill`` 为什么也先不定：契约照**已核实的事实**写，不照想象写。它的参数
    形状取决于各家 ``draft`` 命令的实际选项，没在真机上核实过就写死，后来者
    会照着一个错的契约去实现 —— 签名定错比没有签名更坏。
    """
    public = {n for n in dir(PublishProvider) if not n.startswith("_")}
    extra = public - {"name", "probe"}
    assert not extra, (
        f"发布协议只允许 name + probe，出现了 {sorted(extra)}；"
        "加 publish / fill / upload 都需先改 ADR-008 与本测试"
    )
    assert not hasattr(PublishProvider, "publish")


def test_stub_satisfies_the_protocol() -> None:
    """``runtime_checkable`` 真的认结构化实现（否则注入点形同虚设）。"""
    assert isinstance(_StubProvider(AvailabilityState.READY), PublishProvider)
    assert not isinstance(object(), PublishProvider)


# ---------------------------------------------------------------------------
# 3. 解析层
# ---------------------------------------------------------------------------


def test_resolve_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """未设置 → ``None``：发布能力默认**关着**（与 resolve_image 同一规矩）。"""
    monkeypatch.delenv(_ENV_KEY, raising=False)
    assert resolve_publish_provider() is None


def test_resolve_builds_opencli(monkeypatch: pytest.MonkeyPatch) -> None:
    """``opencli`` → OpenCLI Provider；大小写与空白宽容。"""
    monkeypatch.setenv(_ENV_KEY, "  OpenCLI  ")
    provider = resolve_publish_provider()
    assert isinstance(provider, OpenCliPublishProvider)
    assert provider.name == "opencli"


def test_resolve_rejects_typos_instead_of_falling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拼错的渠道名 → ``None``，**不凑合跑**。

    凑合跑的话（比如把未知值当默认渠道），错误会一直留在配置里，直到某天以
    「明明配了却没用」的形式爆出来。
    """
    monkeypatch.setenv(_ENV_KEY, "opencil")
    assert resolve_publish_provider() is None


def test_resolve_honours_custom_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """``STEPWORK_OPENCLI_BIN`` 可指向任意路径（装别处 / 换实现）。"""
    monkeypatch.setenv(_ENV_KEY, "opencli")
    monkeypatch.setenv("STEPWORK_OPENCLI_BIN", "/opt/custom/opencli")
    provider = resolve_publish_provider()
    assert isinstance(provider, OpenCliPublishProvider)
    # 直接读私有字段：这不是「测实现细节」，而是断言 env 真的被读进去了
    # （行为上无法区分「用了这个 binary」和「用了默认值但恰好也探不到」）
    assert provider._binary == "/opt/custom/opencli"  # noqa: SLF001


# ---------------------------------------------------------------------------
# 4. 真 subprocess：纪律与返回值
# ---------------------------------------------------------------------------


async def test_probe_reports_unavailable_when_binary_missing() -> None:
    """没装 → **返回值**说 UNAVAILABLE（不是异常），hint 给出可执行的一步。"""
    provider = OpenCliPublishProvider(binary=_ABSENT_BIN)
    availability = await provider.probe()
    assert availability.state is AvailabilityState.UNAVAILABLE
    assert availability.provider == "opencli"
    assert availability.exit_code is None
    assert "PATH" in availability.detail
    # hint 必须可执行：给出安装命令，而不是「请检查配置」
    assert "npm i -g @jackwener/opencli" in availability.hint


async def test_probe_really_spawns_and_reads_exit_code() -> None:
    """真跑一个可执行文件，真的拿到退出码与输出。

    用 ``sys.executable`` 当 binary：``python doctor`` 必然报「打不开 doctor」
    并以非零码退出 —— 语义上等价于「装了但 doctor 失败」。关键是它证明代码
    真的走到了 subprocess，而不是返回一个写死的常量。
    """
    provider = OpenCliPublishProvider(binary=sys.executable)
    availability = await provider.probe()
    assert availability.state is AvailabilityState.UNAVAILABLE
    assert availability.exit_code is not None
    assert availability.exit_code != 0
    # 输出带回来了（stderr 优先）—— 只报退出码等于让人猜
    assert "doctor" in availability.detail


async def test_probe_timeout_kills_instead_of_hanging() -> None:
    """超时**强杀**并如实说「没有返回」，绝不挂着等。

    0.001s 必然超时。若实现忘了 kill，这个测试会**挂住**而不是失败 ——
    那正是要防的形态：UI 一直转圈，且看不出卡在哪一步。
    """
    provider = OpenCliPublishProvider(binary=sys.executable, timeout_sec=0.001)
    availability = await provider.probe()
    assert availability.state is AvailabilityState.UNAVAILABLE
    assert availability.exit_code is None
    assert "没有返回" in availability.detail


# ---------------------------------------------------------------------------
# 5. handler 与命令链路（走完整 dispatch，证明路由真的注册了）
# ---------------------------------------------------------------------------


async def test_dispatch_reports_unset_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置：点名环境变量，并说清填充包不受影响。"""
    monkeypatch.delenv(_ENV_KEY, raising=False)
    res = await dispatch(_env(), _deps())
    assert res["ok"] is True
    detail = res["detail"]
    assert detail["state"] == "unavailable"
    assert detail["provider"] == ""
    assert _ENV_KEY in detail["detail"]
    assert detail["auto_publish"] is False


async def test_dispatch_names_a_typoed_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拼错时必须**点名那个值**。

    这里曾把「设了但不认识」写成「为空」，真机一跑就露馅：用户明明设了值，
    却被指去查一个不存在的「没配置」问题 —— 报错写错方向比不报还费时间。
    """
    monkeypatch.setenv(_ENV_KEY, "opencil")
    res = await dispatch(_env(), _deps())
    detail = res["detail"]
    assert detail["state"] == "unavailable"
    assert "opencil" in detail["detail"]  # 点名，而不是说「为空」
    assert detail["auto_publish"] is False


@pytest.mark.parametrize(
    "state",
    [AvailabilityState.READY, AvailabilityState.NEED_LOGIN],
)
async def test_dispatch_surfaces_injected_state(state: AvailabilityState) -> None:
    """注入 Provider 后，``READY`` / ``NEED_LOGIN`` 也能在无外部工具的机器上验证。

    断言的不只是 state：``provider`` 与 ``auto_publish`` 也一并锁住 ——
    ADR-008 的「永不自动发布」要**每一个状态**都成立，不能只有失败路径记得带。
    """
    res = await dispatch(_env(), _deps(publish=_StubProvider(state)))
    detail = res["detail"]
    assert detail["state"] == state.value
    assert detail["provider"] == "stub"
    assert detail["auto_publish"] is False


async def test_detail_matches_result_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """出参符合 ``ProbePublishProviderDetail`` 契约（``extra="forbid"``）。

    固定用「必定探不到」的 binary，避免测试结果取决于跑测机器上装没装 opencli。
    """
    monkeypatch.setenv(_ENV_KEY, "opencli")
    monkeypatch.setenv("STEPWORK_OPENCLI_BIN", _ABSENT_BIN)
    res = await dispatch(_env(), _deps())
    ProbePublishProviderDetail.model_validate(res["detail"])
