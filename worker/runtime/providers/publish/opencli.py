"""OpenCLI 发布 Provider（ADR-012 的底座候选）。

**可选依赖**：本模块不 import 任何 Python 包 —— OpenCLI 是 Node CLI，没有
Python 包，只按 PATH 上的可执行文件调用。因此 import 本模块**永不**因为
「没装 opencli」而失败：没装是 :meth:`OpenCliPublishProvider.probe` 的
**返回值**，不是导入期异常。这是 ADR-012「未装 / daemon 未起 / 未登录显式
``UNAVAILABLE`` / ``NEED_LOGIN``，不静默降级」的落地方式 —— 把「可选」做成
运行时可回答的问题，而不是安装期的开关。

**只探状态，不点发布**：本模块只有 ``probe``，没有任何调用 ``publish`` 类
子命令的代码路径。ADR-008 的理由见 :mod:`worker.runtime.providers.publish.base`。

**真机定契约**（2026-09-13，装完 opencli 1.8.7 之后）—— 三个坑，全是单测
永远测不到、只有真机才会暴露的：

1. **判据不能是退出码**：``opencli doctor`` 在「扩展未连接」时也返回 ``0``
   （``dist/src/doctor.js`` 里没有 ``process.exit``）。改用
   ``auth status --site <site> --format json`` —— 结构化输出，且直接回答 fill
   的前提问题（**登录态**）；桥断开时 ``status="error"``、``error`` 里带
   ``BROWSER_CONNECT: ...``。
2. **Windows 上 npm 全局命令是 ``.cmd``**：``CreateProcess`` 只自动补 ``.exe``，
   拿裸名 ``opencli`` 去 spawn 必然 ``FileNotFoundError [WinError 2]`` ——
   典型症状是「``which`` 明明找到了，代码却没用它」。见 :func:`_resolve_argv`。
3. **它慢，而慢得有理由**：扩展没连时 ``auth status`` 会一直等浏览器桥
   （``bridge-readiness.js`` 每 200ms 轮询一次），上限是
   ``DEFAULT_BROWSER_CONNECT_TIMEOUT`` = **45s**
   （``dist/src/browser/config.js``，可由 ``OPENCLI_BROWSER_CONNECT_TIMEOUT``
   覆盖）。实测两次都是 **t+46.1s 才写出第一个字节、紧接着退出、exit 0**，
   stdout 是完整 JSON。⇒ **超时上限必须大于它**，否则会在它写出第一个字节之前
   就把它杀掉，然后把「其实马上就有答案」报成「什么都没产出」。

⚠️ **这一条我先前判错了，记在这里以免再犯。** 我一度断言「它输出完不退出」+
「stdout 非 tty 时全缓冲、重定向到文件连数据都拿不到」，并据此把「轮询到完整
JSON 就 kill」当成主要机制。对照实验把三条都证伪了：

- ``opencli --version`` 经**同一个管道** 0.6s 拿到 6 字节，且正常退出；
- 文件重定向跟管道一样是空的 —— 但原因是**它那时还没写**，不是写不出去；
- 它其实是**写完立刻退出**的；是我 25s 的上限先动手。

现在的事实是：**唯一的问题只是慢**。故做法改为：

1. 主动把 ``OPENCLI_BROWSER_CONNECT_TIMEOUT`` 收紧
   （见 :data:`DEFAULT_CONNECT_TIMEOUT_SEC`，实测 6 → 全程 11s）；
2. 自己的上限留 2× 余量（:data:`DEFAULT_PROBE_TIMEOUT_SEC`）；
3. 轮询收工降级为「万一某版本写完不退」的兜底，不再是主要机制
   （见 :meth:`OpenCliPublishProvider._run`）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from typing import Final

from worker.runtime.providers.publish.base import (
    Availability,
    AvailabilityState,
    classify_exit,
)

#: 我们自己给这个 CLI 的**浏览器连接超时**（秒），经 ``OPENCLI_BROWSER_CONNECT_TIMEOUT``
#: 传给子进程。它自己的默认是 45s —— 那是给「用户开着浏览器、扩展刚装好、正在
#: 握手」这种场景留的；对一个**探测**来说 45s 太长，GUI 上就是一分钟的白等。
#: 8s 足够：扩展真连着的话握手在百毫秒级，连不上则 8s 后如实报「没连上」。
DEFAULT_CONNECT_TIMEOUT_SEC: Final = 8

#: 每个站点的检查超时（秒），经 ``--timeout`` 传给它。
#: 与上面的区别：那个管「等浏览器桥连上」，这个管「单站点的登录态检查跑多久」。
#: 我们**希望它自己兜住**并输出一条结构化的 ``error`` 行 —— 输出是主判据，
#: 让 CLI 说话永远好过我们把它杀掉之后对着一片空白猜。
DEFAULT_SITE_TIMEOUT_SEC: Final = 10

#: 我们自己的上限：超过它就把子进程强杀掉，报「没能产出」。
#: **必须大于上面两个之和加启动开销**，否则就是拿自己的耐心去截断别人的答案。
#: 算一下：8（等桥）+ 10（单站点）+ ~5（node 起 + daemon 冷启，实测）≈ 23s；
#: 取 35s 留够余量。真机实测（扩展未连）全程 11s 就返回了。
DEFAULT_PROBE_TIMEOUT_SEC: Final = 35.0

#: 轮询间隔：多久检查一次「输出是否已成为完整 JSON」（兜底机制，见 ``_run``）。
_POLL_INTERVAL_SEC: Final = 0.15

#: 默认目标站点。V0.x 的主战场是抖音；其它平台用 ``STEPWORK_PUBLISH_SITE`` 覆盖。
DEFAULT_SITE: Final = "douyin"

#: 探不到时给用户的那一步。**必须可执行** ——「请检查配置」不算建议。
_INSTALL_HINT: Final = (
    "要发布能力就装它：npm i -g @jackwener/opencli（需要 Node ≥ 20.18.1），"
    "再起它的 daemon 并装浏览器扩展；不需要就把 "
    "STEPWORK_PUBLISH_PROVIDER 留空（本能力默认关闭）"
)

#: 站点名对不上时的下一步。**刻意不复用 :data:`_INSTALL_HINT`** —— 工具明明
#: 装着，却让用户去装，就是「报错写错方向，白跑一趟」。这是真机验收当场暴露的：
#: ``--site not-a-real-site`` 的那一轮返回的 hint 在教人去 npm 装包。
_UNKNOWN_SITE_HINT: Final = (
    "先确认站点名：不带 --site 跑一次 `opencli auth status --format json` 会列出"
    "它支持的全部站点（条目多、每站都要等一次连接超时，会慢）；确认后把 "
    "STEPWORK_PUBLISH_SITE 改成它认识的名字"
)

#: 桥通但扩展没连时的下一步（实测 ``error`` 字段原文即 ``BROWSER_CONNECT: ...``）。
_EXTENSION_HINT: Final = (
    "opencli 的浏览器扩展没连上。装扩展：从 "
    "https://github.com/jackwener/opencli/releases 下载 → "
    "chrome://extensions/ 开「开发者模式」→「加载已解压的扩展程序」；"
    "装好后若仍不连，跑 `opencli daemon restart`"
)


def _resolve_argv(path: str, *args: str) -> list[str]:
    """把「PATH 上找到的路径」变成可直接 spawn 的 argv。

    Windows 上 npm 全局命令是 ``.cmd`` 批处理，``CreateProcess`` 不会自动补
    ``.CMD``（只补 ``.EXE``）→ 必须显式经 ``cmd /c`` 执行。POSIX 上直接执行。
    """
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return [comspec, "/c", path, *args]
    return [path, *args]


def _child_env(connect_timeout_sec: int) -> dict[str, str]:
    """给子进程的环境：**收紧它的浏览器连接超时**，并尊重用户已有的设置。

    **纯函数**（吃 dict / 吐 dict，不碰 ``os.environ``）—— 这样「我们把哪个变量
    设成了什么」可以被单测钉住，而不是靠读一遍 ``_run`` 相信它。

    ``setdefault`` 而不是直接赋值：用户若自己导出了
    ``OPENCLI_BROWSER_CONNECT_TIMEOUT``，那是他的选择，我们不覆盖 —— 与
    ``STEPWORK_OPENCLI_BIN`` / ``STEPWORK_PUBLISH_SITE`` 一个规矩。
    """
    env = dict(os.environ)
    env.setdefault("OPENCLI_BROWSER_CONNECT_TIMEOUT", str(connect_timeout_sec))
    return env


def _is_complete_json(buf: bytearray) -> bool:
    """``buf`` 是否已是一份可解析的完整 JSON。

    **兜底信号**，不是主要机制。正常路径上用不到它：那个 CLI 是写完就退出的，
    真正的收工条件是**进程退出**。留着它是防「某天它改成写完不退」——
    那时如果没有这条，我们会一直等到自己的上限，然后把一份已经完整的 JSON
    当成「什么都没产出」丢掉。
    """
    if not buf:
        return False
    try:
        json.loads(buf.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return True


def classify_auth_payload(
    payload: object, site: str
) -> tuple[AvailabilityState, str] | None:
    """把 ``auth status --format json`` 的解析结果翻成「三态 + 一句话」。

    **纯函数**（不碰进程、不碰网络）—— 判据层单独可测，这是本仓
    ``features/*/viewModel.ts`` 与 handler 的一贯分层：契约读取集中在一处。

    ``status`` 的取值域来自 ``opencli auth status --help`` 的 ``--only``
    选项：``logged-in`` / ``not-logged-in`` / ``unknown`` / ``error``。

    Returns:
        ``(状态, 可读说明)``；**payload 形状不认识时返回 ``None``** ——
        不认识的形状一律不猜（猜错方向比不报还费时间）。
    """
    rows = payload if isinstance(payload, list) else [payload]
    row: dict[str, object] | None = None
    for item in rows:
        if isinstance(item, dict):
            item_site = str(item.get("site") or "").strip().lower()
            if item_site == site.strip().lower():
                row = item
                break
    if row is None:
        return None

    status = str(row.get("status") or "").strip().lower()
    error = str(row.get("error") or "").strip()
    identity = str(row.get("identity") or "").strip()
    if status == "logged-in":
        who = f"（{identity}）" if identity else ""
        return AvailabilityState.READY, f"{site} 已登录{who}"
    if status == "not-logged-in":
        return AvailabilityState.NEED_LOGIN, f"{site} 未登录"
    if status in ("error", "unknown"):
        return AvailabilityState.UNAVAILABLE, f"{site} 状态 {status}：{error or '无原因'}"
    return None


class OpenCliPublishProvider:
    """经 PATH 调用 ``opencli`` 的发布 Provider（本仓不引其代码）。"""

    name = "opencli"

    def __init__(
        self,
        *,
        binary: str = "opencli",
        site: str | None = None,
        timeout_sec: float = DEFAULT_PROBE_TIMEOUT_SEC,
        connect_timeout_sec: int = DEFAULT_CONNECT_TIMEOUT_SEC,
        site_timeout_sec: int = DEFAULT_SITE_TIMEOUT_SEC,
    ) -> None:
        """Args:
            binary: 可执行文件名或路径（可用 ``STEPWORK_OPENCLI_BIN`` 覆盖）。
            site: 目标站点（``douyin`` / ``xiaohongshu`` …）。默认取
                ``STEPWORK_PUBLISH_SITE``，再退到 :data:`DEFAULT_SITE`。
            timeout_sec: 我们等它的上限，超过就强杀。
            connect_timeout_sec: 传给它的浏览器连接超时（见同名模块常量）。
            site_timeout_sec: 传给它的单站点检查超时（见同名模块常量）。
        """
        self._binary = binary
        resolved_site = site or os.environ.get("STEPWORK_PUBLISH_SITE") or DEFAULT_SITE
        self._site = resolved_site.strip().lower()
        self._timeout = timeout_sec
        self._connect_timeout = connect_timeout_sec
        self._site_timeout = site_timeout_sec

    async def probe(self) -> Availability:
        """跑 ``<binary> auth status --site <site> --format json`` 并判三态。

        ``shutil.which`` 先探一次是为了把「没装」和「装了但桥不通 / 未登录」
        分开 —— 三者的 ``hint`` 完全不同（一个要去装，一个要装扩展，一个要去
        登录），混成一句「不可用」用户就得自己试。
        """
        path = shutil.which(self._binary)
        if path is None:
            return Availability(
                state=AvailabilityState.UNAVAILABLE,
                provider=self.name,
                detail=f"{self._binary} 不在 PATH 上，无法把内容填进平台表单",
                hint=_INSTALL_HINT,
            )

        code, stdout, stderr = await self._run(
            "auth",
            "status",
            "--site",
            self._site,
            "--format",
            "json",
            "--timeout",
            str(self._site_timeout),
        )

        # **输出是主判据**，退出码只作兜底 —— 那个 CLI 的退出码不表达状态
        # （``doctor`` 在桥断开时照样返 ``0``），且我们超时强杀时根本拿不到。
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            by_code = classify_exit(code)
            if by_code is not None and by_code is not AvailabilityState.READY:
                return Availability(
                    state=by_code,
                    provider=self.name,
                    detail=self._describe_failure(code, stdout, stderr),
                    hint=self._hint_for(by_code),
                    exit_code=code,
                )
            raw = (stdout or stderr or "无输出").strip()[:200]
            return Availability(
                state=AvailabilityState.UNAVAILABLE,
                provider=self.name,
                detail=f"{self._binary} auth status 没有产出可解析的 JSON：{raw}",
                hint=_INSTALL_HINT,
                exit_code=code,
            )

        verdict = classify_auth_payload(payload, self._site)
        if verdict is None:
            rows = payload if isinstance(payload, list) else [payload]
            return Availability(
                state=AvailabilityState.UNAVAILABLE,
                provider=self.name,
                detail=(
                    f"{self._binary} auth status 的输出里没有站点 {self._site!r}："
                    f"这一轮返回 {len(rows)} 条，逐条看过，site 都不是这个名字"
                ),
                # ⚠️ 这里**不能**给安装提示：工具已经装好了（我们刚跑的就是它），
                # 让用户去装是白跑一趟。见 _UNKNOWN_SITE_HINT。
                hint=_UNKNOWN_SITE_HINT,
                exit_code=code,
            )

        state, detail = verdict
        return Availability(
            state=state,
            provider=self.name,
            detail=detail,
            hint=self._hint_for(state),
            exit_code=code,
        )

    async def _run(self, *args: str) -> tuple[int | None, str, str]:
        """跑 ``<binary> <args...>``，返回 ``(退出码, stdout, stderr)``。

        进程纪律照 :class:`~worker.runtime.agents.mcp_client.McpStdioClient`
        （``stdin=DEVNULL`` 绝不让它等一个永远不来的回车；超时强杀），外加两条
        **真机踩出来的**：

        1. **收紧它的连接超时**：见 :func:`_child_env`。不收紧的话，「扩展没连」
          这种最常见的状态要等满它自己的 45s（实测 t+46.1s 才出第一个字节），
          对 GUI 来说跟卡死没区别。
        2. **上限只当保险丝**：:data:`DEFAULT_PROBE_TIMEOUT_SEC` 比它自己的
           上限宽，所以正常路径下**等的是它退出**，不是我们杀它。杀掉是异常情况，
           杀掉时把**已经读到的**输出一并带回（``pump`` 就地追加），不丢证据 ——
           `communicate()` 在超时被取消时会丢掉已读内容，故不走它。

        收工条件按优先级：**进程退出** > 看到完整 JSON（兜底）> 到上限强杀。
        阅读顺序上把「完整 JSON」放前面是刻意的：它是**唯一**能在「它写完却不退」
        时救回答案的信号，而进程退出在那种情况下永远不会发生。

        Returns:
            ``(退出码, stdout, stderr)``。``退出码为 None`` 有两种含义：进程
            没自然退出（我们到上限强杀的），或压根没起来 —— 所以调用方应以
            **stdout 为主判据**，退出码只作兜底。
        """
        argv = _resolve_argv(shutil.which(self._binary) or self._binary, *args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_child_env(self._connect_timeout),
            )
        except OSError as e:
            # 起不来（权限位丢了 / PATH 竞态）：如实带回类型名，别只留一句失败
            return None, "", f"{type(e).__name__}: {e}"

        assert proc.stdout is not None and proc.stderr is not None  # noqa: S101
        out = bytearray()
        err = bytearray()

        async def pump(stream: asyncio.StreamReader, sink: bytearray) -> None:
            """把流读进 sink。取消时已读到的内容保留（sink 是就地追加）。"""
            with contextlib.suppress(asyncio.CancelledError, OSError):
                while True:
                    chunk = await stream.read(4096)
                    if not chunk:
                        return
                    sink.extend(chunk)

        pumps = [
            asyncio.create_task(pump(proc.stdout, out)),
            asyncio.create_task(pump(proc.stderr, err)),
        ]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        while loop.time() < deadline:
            if _is_complete_json(out):
                break
            if proc.returncode is not None:
                break
            await asyncio.sleep(_POLL_INTERVAL_SEC)

        # **收工那一刻**的自然退出码。必须在这里取：下一段的 kill 会把它覆盖成
        # 「被杀的码」，而 ``Availability.exit_code`` 承诺的是**它自己的**退出码
        # ——报一个我们自己造成的数，等于把诊断线索写坏（真机形态：它写完 JSON
        # 立刻退出，轮询常先看到 JSON，若用「是否因退出而收工」判断就会丢掉那个 0）。
        natural_code = proc.returncode
        if proc.returncode is None:
            proc.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), 5.0)
        for task in pumps:
            task.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)

        return (
            natural_code,
            out.decode("utf-8", errors="replace").strip(),
            err.decode("utf-8", errors="replace").strip(),
        )

    def _describe_failure(self, code: int | None, stdout: str, stderr: str) -> str:
        """非 0 退出 / 起不来 / 到上限时的一句话原因。"""
        raw = (stderr or stdout or "无输出").strip()[:200]
        if code is None:
            return (
                f"{self._binary} auth status 没能在 {self._timeout:.0f}s 内产出完整"
                f"输出（已强杀）。它连不上浏览器桥时会自己等 45s，我们已把这项压到"
                f" {self._connect_timeout}s，仍超时说明它卡在更早的地方：{raw}"
            )
        return f"{self._binary} auth status 退出码 {code}：{raw}"

    def _hint_for(self, state: AvailabilityState) -> str:
        """给**可执行的一步**：三种状态的修法完全不同。"""
        if state is AvailabilityState.READY:
            return "可以填充；填完停在预览页，最终点发布仍由你手动完成（ADR-008）"
        if state is AvailabilityState.NEED_LOGIN:
            return (
                f"在 opencli 连接的浏览器里登录 {self._site}，然后重新检测"
                f"（`{self._binary} auth status --site {self._site}` 可看原始状态）"
            )
        return f"{_EXTENSION_HINT}。若扩展正常，再跑 `{self._binary} doctor` 看原始报错"
