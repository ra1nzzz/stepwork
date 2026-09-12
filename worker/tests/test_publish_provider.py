"""发布 Provider 可用性探测测试（S7 第一段：接口先于实现）。

锁死四件事：

1. **三态只有三个**（``ready`` / ``unavailable`` / ``need_login``）——
   多出来的中间态正是静默降级藏身的地方；
2. **退出码只表达「明确的失败」**，``0`` 什么都不证明 —— 真机上 ``opencli
   doctor`` 在桥断开时照样 ``exit 0``，所以 ``READY`` 必须另有判据（解析
   ``auth status --format json``）；把 ``0`` 当 ``READY`` 会把「不可用」
   报成「可填充」（假阳性比漏报更坏）；
3. **协议里没有 ``publish``**（ADR-008 的结构性保证）—— 这条断言是
   **变更检测器**，不是现状描述：有人日后给协议加动作时它当场红；
4. **真 subprocess 的纪律**：能拿到退出码与输出、超时**强杀不挂起**（且强杀时
   已读到的输出不丢）、没装时走返回值而不是异常、以及**我们给的超时真的到了
   子进程手里**（不是只构造了个 env 字典）。
"""

from __future__ import annotations

import os
import sys
import time
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
from worker.runtime.providers.publish.opencli import (
    OpenCliPublishProvider,
    classify_auth_payload,
)
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


def test_classify_exit_only_reports_explicit_failures() -> None:
    """退出码**只用来表达明确的失败**；``0`` 什么都不证明。

    2026-09-13 真机修正：``opencli doctor`` 在「扩展未连接」时照样 ``exit 0``
    （``dist/src/doctor.js`` 里没有 ``process.exit``）—— 所以把 ``0`` 当
    ``READY`` 会把「桥断了」报成「可以填充」。**假阳性比漏报更坏**：用户以为
    排上了，实际什么都没发生。

    这里刻意断言 ``classify_exit(0) is None`` 而**不是** ``is UNAVAILABLE``：
    ``None`` 的语义是「退出码说不清，请另找判据」，逼调用方去看输出；
    若在这里直接回落成 UNAVAILABLE，那「其实可用」也会被误报，一样是错的。
    """
    assert classify_exit(EX_OK) is None
    # 77 = 权限不足：装好了、桥也通，只差登录 —— 用户做一步就能好
    assert classify_exit(EX_NOPERM) is AvailabilityState.NEED_LOGIN
    # 其余一律保守：不认识的就当不可用，不猜（66「结果为空」不再当作 READY）
    for code in (EX_NOINPUT, EX_UNAVAILABLE, EX_TEMPFAIL, EX_CONFIG, 1, 2, 127):
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

    用 ``sys.executable`` 当 binary：``python auth status --site …`` 必然报
    「打不开 auth」并以非零码退出。关键是它证明代码真的走到了 subprocess，
    而不是返回一个写死的常量 —— 顺带证明 **argv 用的是完整路径**：真机上这里
    曾因「``which`` 明明找到了 ``opencli.CMD``、代码却仍用裸名去 spawn」而在
    Windows 上必然 ``FileNotFoundError``（见 ``opencli._resolve_argv``）。
    """
    provider = OpenCliPublishProvider(binary=sys.executable)
    availability = await provider.probe()
    assert availability.state is AvailabilityState.UNAVAILABLE
    assert availability.exit_code is not None
    assert availability.exit_code != 0
    # stdout / stderr 都带回来了 —— 只报退出码等于让人猜
    assert "auth status" in availability.detail


def _fake_opencli(tmp_path: Path, *, stdout: str, code: int) -> str:
    """造一个「打印一行并以指定码退出」的假 opencli，返回其路径。

    每平台一份壳：``cmd`` 的 ``echo`` 原样输出，而 POSIX 的 ``echo`` 会吞掉
    双引号 → 必须用 ``printf '%s\\n' '…'``，否则 JSON 到手就坏了。
    """
    if os.name == "nt":
        script = tmp_path / "fake-opencli.cmd"
        script.write_text(
            f"@echo off\necho {stdout}\nexit /b {code}\n", encoding="ascii"
        )
    else:
        script = tmp_path / "fake-opencli"
        script.write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{stdout}'\nexit {code}\n", encoding="utf-8"
        )
        script.chmod(0o755)
    return str(script)


def _fake_opencli_echoing_env(tmp_path: Path, var_name: str) -> str:
    """造一个把指定环境变量的**实际取值**回显进 JSON 的假 opencli。

    POSIX 的 ``printf`` 用单引号包 JSON、再为变量单独开一段引号 ——
    ``echo`` 会吞掉双引号，而少了引号就不是 JSON 了。
    下面那段三引号拼接已在 Git Bash 里**手工真跑过**，产出能被 ``json.load`` 解析；
    但要注意 **Windows 上走不到这个分支**，它只在 CI 的 Linux 上被执行 ——
    本机跑绿不代表这段被覆盖过。
    """
    if os.name == "nt":
        script = tmp_path / "fake-opencli-env.cmd"
        script.write_text(
            "@echo off\n"
            f'echo [{{"site":"douyin","status":"logged-in","identity":"%{var_name}%"}}]\n'
            "exit /b 0\n",
            encoding="ascii",
        )
    else:
        script = tmp_path / "fake-opencli-env"
        body = (
            "printf '%s\\n' "
            "'[{\"site\":\"douyin\",\"status\":\"logged-in\",\"identity\":\"'"
            f"\"${var_name}\""
            "'\"}]'\n"
        )
        script.write_text(f"#!/bin/sh\n{body}exit 0\n", encoding="utf-8")
        script.chmod(0o755)
    return str(script)


def test_auth_payload_maps_three_states() -> None:
    """判据层是纯函数。``status`` 取值域来自 ``auth status --help`` 的 ``--only``。"""
    assert classify_auth_payload(
        [{"site": "douyin", "status": "logged-in", "identity": "tester"}], "douyin"
    ) == (AvailabilityState.READY, "douyin 已登录（tester）")
    assert classify_auth_payload(
        [{"site": "douyin", "status": "not-logged-in"}], "douyin"
    ) == (AvailabilityState.NEED_LOGIN, "douyin 未登录")


def test_auth_payload_shapes_it_does_not_know_return_none() -> None:
    """**不认识的形状一律不猜** —— 猜错方向比不报还费时间。"""
    assert (
        classify_auth_payload([{"site": "weibo", "status": "logged-in"}], "douyin")
        is None
    )
    assert classify_auth_payload([{"site": "douyin", "status": "?"}], "douyin") is None
    assert classify_auth_payload({"detail": "nope"}, "douyin") is None
    assert classify_auth_payload("plain text", "douyin") is None


async def test_probe_reads_real_three_states_from_stdout(tmp_path: Path) -> None:
    """端到端：假 opencli 打印真契约 JSON，三条分支逐条走通。

    ``NEED_LOGIN`` 此前只有「注入 stub」这一条路径覆盖，而 stub 绕过了真正的
    解析 —— 等于三态里有一条长期无人看守。这里用真 subprocess + 真 JSON 钉住。
    """
    cases = [
        (
            '[{"site":"douyin","status":"logged-in","identity":"tester"}]',
            AvailabilityState.READY,
        ),
        ('[{"site":"douyin","status":"not-logged-in"}]', AvailabilityState.NEED_LOGIN),
        (
            '[{"site":"douyin","status":"error","error":"BROWSER_CONNECT: x"}]',
            AvailabilityState.UNAVAILABLE,
        ),
    ]
    for stdout, expected in cases:
        provider = OpenCliPublishProvider(
            binary=_fake_opencli(tmp_path, stdout=stdout, code=0)
        )
        availability = await provider.probe()
        assert availability.state is expected, stdout


async def test_exit_zero_alone_is_never_ready(tmp_path: Path) -> None:
    """**假阳性回归**：退出码 ``0`` + 非 JSON 输出 → ``UNAVAILABLE``，不是 ``READY``。

    这正是真机上的形态：``opencli doctor`` 在桥断开时照样 ``exit 0``。旧实现
    只看退出码，会把「不可用」报成「可以填充」—— 而假阳性比漏报更坏。
    """
    provider = OpenCliPublishProvider(
        binary=_fake_opencli(tmp_path, stdout="not json at all", code=0)
    )
    availability = await provider.probe()
    assert availability.state is AvailabilityState.UNAVAILABLE
    assert "可解析的 JSON" in availability.detail


async def test_unparseable_output_with_nonzero_exit_is_unavailable(
    tmp_path: Path,
) -> None:
    """输出不可解析 + 非 ``0`` 退出 → ``UNAVAILABLE``，并带上退出码。

    优先级要记住：**输出是主判据，退出码只作兜底**。真机上 ``auth status``
    在桥断开时既会输出完整 JSON、又返回 ``0`` —— 所以退出码在两条路上都
    判不出可用性（``0`` 不敢当 READY，非 0 又几乎见不到）。
    """
    provider = OpenCliPublishProvider(
        binary=_fake_opencli(tmp_path, stdout="boom", code=3)
    )
    availability = await provider.probe()
    assert availability.state is AvailabilityState.UNAVAILABLE
    assert availability.exit_code == 3


def test_child_env_bounds_the_connect_timeout() -> None:
    """``_child_env`` 把 ``OPENCLI_BROWSER_CONNECT_TIMEOUT`` 收紧到我们的值。

    为什么非要做这件事：它自己的默认是 **45s**（``browser/config.js``），
    扩展没连时 ``auth status`` 就要等满这 45s 才写第一个字节 —— 实测 t+46.1s。
    对 GUI 来说这跟卡死没区别，而「扩展没连」恰恰是最常见的状态。
    """
    from worker.runtime.providers.publish.opencli import _child_env  # noqa: PLC0415

    assert _child_env(8)["OPENCLI_BROWSER_CONNECT_TIMEOUT"] == "8"


def test_child_env_respects_a_value_the_user_already_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户自己导出过就不覆盖 —— 与 ``STEPWORK_OPENCLI_BIN`` 一个规矩。

    覆盖用户的显式设置，会让「我明明调过这个变量却没生效」变成一次纯浪费的
    排查。
    """
    from worker.runtime.providers.publish.opencli import _child_env  # noqa: PLC0415

    monkeypatch.setenv("OPENCLI_BROWSER_CONNECT_TIMEOUT", "30")
    assert _child_env(8)["OPENCLI_BROWSER_CONNECT_TIMEOUT"] == "30"


async def test_the_child_really_receives_the_bounded_timeout(tmp_path: Path) -> None:
    """端到端：假 opencli 把该变量的**实际取值**回显进 JSON。

    只测 ``_child_env`` 的返回值不够 —— 那证明的是「我们构造了一个 env 字典」，
    不是「子进程收到了它」。中间隔着 ``create_subprocess_exec(env=...)`` 这一步，
    而这一步恰恰是会写错的地方（忘了传 / 传成了位置参数）。
    """
    provider = OpenCliPublishProvider(
        binary=_fake_opencli_echoing_env(
            tmp_path, "OPENCLI_BROWSER_CONNECT_TIMEOUT"
        ),
        connect_timeout_sec=8,
    )
    availability = await provider.probe()
    assert availability.state is AvailabilityState.READY
    # identity 里回显的就是子进程看到的那个值
    assert "（8）" in availability.detail


async def test_unknown_site_does_not_get_the_install_hint(tmp_path: Path) -> None:
    """站点名对不上时，hint **不能**是「去装它」。

    真机验收当场暴露的：``--site not-a-real-site`` 那一轮返回的 hint 在教用户
    ``npm i -g`` —— 可工具明明装着（我们刚跑的就是它）。**报错写错方向，比不报
    还费时间**：用户会去重装一个已经装好的东西，然后在原地打转。
    """
    provider = OpenCliPublishProvider(
        binary=_fake_opencli(
            tmp_path,
            stdout='[{"site":"weibo","status":"logged-in"}]',
            code=0,
        ),
        site="douyin",
    )
    availability = await provider.probe()
    assert availability.state is AvailabilityState.UNAVAILABLE
    assert "npm i -g" not in availability.hint
    assert "站点" in availability.detail
    # 反过来也要钉住：真·没装的场景**必须**给安装命令（否则用户不知道装什么）
    missing = await OpenCliPublishProvider(binary=_ABSENT_BIN).probe()
    assert "npm i -g @jackwener/opencli" in missing.hint


async def test_probe_reports_the_child_own_exit_code(tmp_path: Path) -> None:
    """它写完 JSON 就退出时，``exit_code`` 必须是**它自己的**退出码。

    回归价值：曾经的实现只在「因为我们观察到进程退出才收工」时才记退出码，
    而轮询常常先看到完整 JSON —— 于是这条最常见路径上 ``exit_code`` 恒为
    ``None``，而 ``None`` 的文档含义是「超时被杀 / 没装」。**一个我们自己造成
    的假象，比没有这个字段更坏**：它会让排查的人去查不存在的超时。
    """
    provider = OpenCliPublishProvider(
        binary=_fake_opencli(
            tmp_path,
            stdout='[{"site":"douyin","status":"logged-in"}]',
            code=0,
        )
    )
    availability = await provider.probe()
    assert availability.state is AvailabilityState.READY
    assert availability.exit_code == 0


async def test_run_kills_a_hung_child_and_keeps_what_it_already_said() -> None:
    """真·挂住的子进程：到上限**强杀**，且**已读到的输出不丢**。

    这是选择 ``pump`` 而不是 ``communicate()`` 的全部理由：``communicate()``
    在超时被取消时把已读内容一起丢掉，排查时手上就只剩一句「超时」。这里让
    子进程先吐一行再睡 30s，断言那行还在。
    """
    provider = OpenCliPublishProvider(binary=sys.executable, timeout_sec=0.5)
    started = time.monotonic()
    code, out, _ = await provider._run(  # noqa: SLF001
        "-c", "import time; print('half a word', flush=True); time.sleep(30)"
    )
    elapsed = time.monotonic() - started
    assert code is None, "被强杀时拿不到自然退出码"
    assert "half a word" in out, "kill 之前读到的输出必须留下"
    assert elapsed < 10, f"必须真杀，不能挂着等：{elapsed:.1f}s"


def _fake_opencli_that_never_ends(tmp_path: Path) -> str:
    """造一个**永不结束**的假 opencli（验证保险丝真的会熔断）。

    Windows 上刻意用 ``cmd`` 自己的 ``goto`` 死循环，而不是 ``ping -n 30``：
    ``ping`` 是孙进程，杀掉 ``cmd.exe`` 之后它会变成孤儿再活 30s；``goto``
    循环就是被 kill 的那个进程本身，测试不留尾巴。
    """
    if os.name == "nt":
        script = tmp_path / "fake-opencli-hang.cmd"
        script.write_text("@echo off\n:loop\ngoto loop\n", encoding="ascii")
    else:
        script = tmp_path / "fake-opencli-hang"
        script.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
        script.chmod(0o755)
    return str(script)


async def test_probe_timeout_kills_instead_of_hanging(tmp_path: Path) -> None:
    """到上限时**强杀**并如实说「没能产出」，绝不挂着等。

    用一个真会挂住的假 opencli。若实现忘了 kill，这个测试会**挂住**而不是
    失败 —— 那正是要防的形态：UI 一直转圈，且看不出卡在哪一步。
    """
    provider = OpenCliPublishProvider(
        binary=_fake_opencli_that_never_ends(tmp_path), timeout_sec=0.5
    )
    started = time.monotonic()
    availability = await provider.probe()
    elapsed = time.monotonic() - started
    assert availability.state is AvailabilityState.UNAVAILABLE
    assert availability.exit_code is None
    assert "没能在" in availability.detail
    assert elapsed < 10, f"必须真杀，不能挂着等：{elapsed:.1f}s"


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
