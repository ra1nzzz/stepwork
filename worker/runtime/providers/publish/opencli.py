"""OpenCLI 发布 Provider（ADR-012 的底座候选）。

**可选依赖**：本模块不 import 任何 Python 包 —— OpenCLI 是 Node CLI，没有
Python 包，只按 PATH 上的可执行文件调用。因此 import 本模块**永不**因为
「没装 opencli」而失败：没装是 :meth:`OpenCliPublishProvider.probe` 的
**返回值**，不是导入期异常。这是 ADR-012「未装 / daemon 未起 / 未登录显式
``UNAVAILABLE`` / ``NEED_LOGIN``，不静默降级」的落地方式 —— 把「可选」做成
运行时可回答的问题，而不是安装期的开关。

**只探状态，不点发布**：本模块只有 ``probe``，没有任何调用 ``publish`` 类
子命令的代码路径。ADR-008 的理由见 :mod:`worker.runtime.providers.publish.base`。
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Final

from worker.runtime.providers.publish.base import (
    EX_TEMPFAIL,
    EX_UNAVAILABLE,
    Availability,
    AvailabilityState,
    classify_exit,
)

#: 探测超时。``opencli doctor`` 要连本机 daemon、可能还要触发扩展握手，
#: 给足 15s；超时即杀并报 ``UNAVAILABLE`` —— 宁可说「探不到」，也不让命令
#: 挂在这里无限等（那会让 UI 一直转圈，且看不出是哪一步卡住）。
DEFAULT_PROBE_TIMEOUT_SEC: Final = 15.0

#: 探不到时给用户的那一步。**必须可执行** ——「请检查配置」不算建议。
_INSTALL_HINT: Final = (
    "要发布能力就装它：npm i -g @jackwener/opencli（需要 Node ≥ 20.18.1），"
    "再起它的 daemon 并装浏览器扩展；不需要就把 "
    "STEPWORK_PUBLISH_PROVIDER 留空（本能力默认关闭）"
)


class OpenCliPublishProvider:
    """经 PATH 调用 ``opencli`` 的发布 Provider（本仓不引其代码）。"""

    name = "opencli"

    def __init__(
        self,
        *,
        binary: str = "opencli",
        timeout_sec: float = DEFAULT_PROBE_TIMEOUT_SEC,
    ) -> None:
        """Args:
        binary: 可执行文件名或路径（可用 ``STEPWORK_OPENCLI_BIN`` 覆盖）。
        timeout_sec: ``probe`` 的超时秒数。
        """
        self._binary = binary
        self._timeout = timeout_sec

    async def probe(self) -> Availability:
        """跑 ``<binary> doctor`` 并把退出码翻成三态。

        ``shutil.which`` 先探一次是为了把「没装」和「装了但桥不通」分开 ——
        两者的 ``hint`` 完全不同（一个要去装，一个要去起 daemon / 登录），
        混成一句「不可用」用户就得自己试。
        """
        path = shutil.which(self._binary)
        if path is None:
            return Availability(
                state=AvailabilityState.UNAVAILABLE,
                provider=self.name,
                detail=f"{self._binary} 不在 PATH 上，无法把内容填进平台表单",
                hint=_INSTALL_HINT,
            )
        code, output = await self._run("doctor")
        state = classify_exit(code)
        return Availability(
            state=state,
            provider=self.name,
            detail=self._describe(path, code, output),
            hint=self._hint_for(state, output),
            exit_code=code,
        )

    async def _run(self, subcommand: str) -> tuple[int | None, str]:
        """跑 ``<binary> <subcommand>``，返回 ``(退出码, 输出片段)``。

        进程纪律照 :class:`~worker.runtime.agents.mcp_client.McpStdioClient`：

        - ``stdin=DEVNULL`` —— **绝不继承本进程 stdin**。CLI 工具若在某个分支
          上等输入（确认提示之类），继承 stdin 会让它挂在后台等一个永远不来的
          回车，而调用方只看到「没返回」。本项目真机撞过这类挂起。
        - 超时**强杀**并返回 ``None`` 退出码，不由调用方无限等。
        - stderr 一并带回：外部工具把真实原因（未登录 / daemon 未起）都写在
          stderr 里，只报退出码等于让人猜。

        Returns:
            ``(退出码, 输出)``；``None`` 退出码表示没能拿到（超时被杀 / 起不来）。
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                self._binary,
                subcommand,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            # 起不来（权限位丢了 / PATH 竞态）：如实带回类型名，别只留一句失败
            return None, f"{type(e).__name__}: {e}"
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return None, f"doctor 在 {self._timeout:.0f}s 内没有返回"
        # stderr 优先：真实原因在那里；两路都空时给空串交由 _describe 兜底
        raw = err or out or b""
        return proc.returncode, raw.decode("utf-8", errors="replace").strip()[:500]

    def _describe(self, path: str, code: int | None, output: str) -> str:
        """一句话说清「现在是什么」，带上足以定位的细节。"""
        if code is None:
            return f"{self._binary} 已装在 {path}，但 doctor {output}"
        if code == EX_TEMPFAIL:
            return f"{self._binary} doctor 超时退出（{code}），daemon 可能正忙"
        if code == EX_UNAVAILABLE:
            return (
                f"{self._binary} 已装但服务不可用（退出码 {code}）："
                f"{output or '无输出'}"
            )
        return f"{self._binary} doctor 退出码 {code}：{output or '无输出'}"

    def _hint_for(self, state: AvailabilityState, output: str) -> str:
        """给**可执行的一步**：三种状态的修法完全不同。"""
        if state is AvailabilityState.READY:
            return (
                "可以填充；填完停在预览页，最终点发布仍由你手动完成（ADR-008）"
            )
        if state is AvailabilityState.NEED_LOGIN:
            return "在它的浏览器扩展里登录目标平台，然后重跑本命令"
        return (
            f"{_INSTALL_HINT}。"
            f"若 opencli 已装，先跑 `{self._binary} doctor` 看原始报错"
            f"（上次输出：{output or '无'}）"
        )
