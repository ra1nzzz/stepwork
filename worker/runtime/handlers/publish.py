"""发布域命令路由（PRD-PUB-001~005 + 定时发布）。

此前这是一个 627 行、11 个 ``commandType`` 分支的 if/elif 长链：改发布授权
要翻过变体导出的代码，加新命令只能继续往里塞。现在按子域拆开，这里只剩路由：

- ``publish_variants``：平台变体、导出包、填充包；
- ``publish_authorization``：一次性发布授权、发布结果与证据；
- ``publish_schedule``：定时发布队列。

公共部分（平台白名单、锚点解析、授权校验）在 ``publish_common``。

路由表放在模块级常量而不是 if/elif：新增命令时忘了加路由会直接落到
``UNKNOWN_COMMAND``，而不是悄悄走到下一个分支里去。
"""

from __future__ import annotations

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.handlers import (
    publish_authorization,
    publish_schedule,
    publish_variants,
)
from worker.runtime.models import CommandEnvelope, CommandResult

#: commandType → 子模块。与 bus._ROUTES 是两层路由：bus 决定「哪个域」，
#: 这里决定「域内哪个子模块」。
_SUBMODULES = {
    "CreatePlatformVariant": publish_variants,
    "ListPlatformVariants": publish_variants,
    "ExportBundle": publish_variants,
    "BuildPlatformFillPackage": publish_variants,
    "RequestPublishAuthorization": publish_authorization,
    "RecordPublishResult": publish_authorization,
    "ListPublishJobs": publish_authorization,
    "SchedulePublish": publish_schedule,
    "ListScheduledPublishes": publish_schedule,
    "CancelScheduledPublish": publish_schedule,
    "FireDueSchedules": publish_schedule,
}


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """按 commandType 分派到发布子域。"""
    module = _SUBMODULES.get(env.commandType)
    if module is None:
        raise DispatchError(
            "UNKNOWN_COMMAND",
            f"commandType {env.commandType!r} not handled by publish handler",
        )
    result: CommandResult = await module.handle(env, deps)
    return result
