"""发布 Provider 可用性探测（S7；只探状态，不触发任何发布动作）。

命令 ``ProbePublishProvider``。ADR-012 要求未装 / daemon 未起 / 未登录一律
**显式**报出 ``UNAVAILABLE`` / ``NEED_LOGIN``，不静默降级；本命令就是那三种
状态的对外出口 —— 前端靠它决定「发布」入口是可用、灰掉、还是提示去登录。

**不建 job**：这是秒级的探测，既不需要进度也不需要取消，照 ``ListMcpTools``
这类只读命令的先例直接返回。（本项目有过反例：把不属于长任务的东西塞进 job
框架，结果 job 永远停在 RUNNING。）
"""

from __future__ import annotations

import os
from typing import Any

from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult
from worker.runtime.providers.publish.base import Availability, AvailabilityState
from worker.runtime.providers.resolve import resolve_publish_provider

#: 与 :func:`~worker.runtime.providers.resolve.resolve_publish_provider` 读的是
#: 同一个键。这里再读一次不是为了取配置，只是为了**把「没设」和「设了但不
#: 认识」分开** —— 两者的修法完全不同。
_ENV_KEY = "STEPWORK_PUBLISH_PROVIDER"


def _unconfigured(raw: str) -> Availability:
    """没有可用 Provider 时的答复。

    分两种说，因为修法不同：

    - **键设了但不认识**（如 ``opencil`` 拼错）→ **点名那个值**。这里曾写成
      「为空」，真机一跑就露馅：用户明明设了值，却被指去查一个不存在的
      「没配置」问题 —— 报错写错方向比不报还费时间。
    - 键为空 → 能力关着，**什么都不用修**；同时要说清这不妨碍生成填充包，
      否则用户会以为发布链路整条废了。

    「没配」不是错误，是正常选择，故用返回值表达而非抛异常。
    """
    if raw:
        return Availability(
            state=AvailabilityState.UNAVAILABLE,
            provider="",
            detail=f"不认识的发布 Provider：{raw!r}（本仓目前只认 opencli）",
            hint=(
                "改成 STEPWORK_PUBLISH_PROVIDER=opencli，或清空它关掉本能力。"
                "拼错的值不会凑合跑 —— 这是有意的：凑合跑的话错误会一直留在"
                "配置里，直到某天以「明明配了却没用」的形式爆出来"
            ),
        )
    return Availability(
        state=AvailabilityState.UNAVAILABLE,
        provider="",
        detail="没有配置发布 Provider（STEPWORK_PUBLISH_PROVIDER 为空）",
        hint=(
            "要用发布能力：设 STEPWORK_PUBLISH_PROVIDER=opencli 并安装 opencli；"
            "不用就保持为空 —— 生成填充包（publish fill）不受影响"
        ),
    )


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """解析 Provider 并探测三态可用性。

    不碰数据库、不建 job：可用性是**环境**的函数（PATH、daemon、登录态），
    与 workspace 状态无关。``deps.repos`` 因此未被使用，但 ``deps.publish``
    是注入点 —— 测试与主装配靠它替换 Provider，不必去改环境变量。
    """
    raw = (os.environ.get(_ENV_KEY) or "").strip()
    # 注入优先、env 解析兜底（与 illustrate_scenes 的 ``... or deps.image`` 同构）
    provider = deps.publish or resolve_publish_provider()
    availability = (
        await provider.probe() if provider is not None else _unconfigured(raw)
    )
    detail: dict[str, Any] = {
        "state": availability.state.value,
        "provider": availability.provider,
        "detail": availability.detail,
        "hint": availability.hint,
        # 仅用于诊断；None = 没拿到（没装 / 超时被杀）
        "exit_code": availability.exit_code,
        # ADR-008：任何状态下都不自动发布。随出参下发，前端不必猜
        "auto_publish": False,
    }
    return CommandResult(ok=True, commandId=env.commandId, detail=detail)
