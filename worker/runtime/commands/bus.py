"""命令总线（W3-W4 Batch 0 + SET.5 设置页）。

路由规则（懒加载 handler 模块，规避 ``bus`` ↔ ``handlers`` 循环依赖）：

- ``ImportSource``   → ``worker.runtime.handlers.import_source``
- ``TranscribeSource``→ ``worker.runtime.handlers.transcribe_source``
- ``AnalyzeSource``  → ``worker.runtime.handlers.analyze_source``
- ``GetConfig`` / ``UpdateConfig`` → ``worker.runtime.handlers.config``
  （写配置 ``UpdateConfig`` 仅允许 ``user`` / ``desktop`` 两类 actor；
  读配置 ``GetConfig`` 返回掩码视图，对任何合法 actor 开放，见 ``_ALLOWED_CONFIG_ACTORS``）
"""

from __future__ import annotations

import importlib
from typing import Any

from worker.runtime.commands.envelope import EnvelopeError, parse_envelope
from worker.runtime.models import CommandResult

# commandType -> handler 模块路径（参数名 ``handle(env, deps)``）
_ROUTES: dict[str, str] = {
    "ImportSource": "worker.runtime.handlers.import_source",
    "TranscribeSource": "worker.runtime.handlers.transcribe_source",
    "AnalyzeSource": "worker.runtime.handlers.analyze_source",
    "CreateRenderJob": "worker.runtime.handlers.render_source",
    "CancelJob": "worker.runtime.handlers.cancel_job",
    "GenerateTopic": "worker.runtime.handlers.generate_topic",
    "GenerateScript": "worker.runtime.handlers.generate_script",
    "SaveScript": "worker.runtime.handlers.save_script",
    "GetConfig": "worker.runtime.handlers.config",
    "UpdateConfig": "worker.runtime.handlers.config",
    "ListProjects": "worker.runtime.handlers.queries",
    "GetProject": "worker.runtime.handlers.queries",
    "GetJobStatus": "worker.runtime.handlers.queries",
}

# 写配置（UpdateConfig）仅允许来自「用户态 / 桌面壳」的 actor（三角色 P0 安全模型）；
# 读配置（GetConfig）返回掩码视图（``••••`` + ``hasKey:bool``），无任何密钥外泄风险，
# 故对任何合法 actor 开放。MCP 不越权的根保证是「MCP 永不注册 UpdateConfig」（tool 集边界）。
_ALLOWED_CONFIG_ACTORS: tuple[str, ...] = ("user", "desktop")


class DispatchError(Exception):
    """handler 内抛出的领域错误（转为 CommandResult.ok=False）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


async def dispatch(raw: dict[str, Any], deps: Any) -> dict[str, Any]:
    """校验信封并路由到对应 handler。

    Args:
        raw: 调用方传入的原始命令 dict。
        deps: 注入依赖（``Deps``：repos / ingest / asr / ai）。

    Returns:
        :class:`CommandResult` 的 ``model_dump()`` 字典。
    """
    try:
        env = parse_envelope(raw)
    except EnvelopeError as e:
        return CommandResult(ok=False, error=str(e)).model_dump()

    module_path = _ROUTES.get(env.commandType)
    if module_path is None:
        return CommandResult(
            ok=False, commandId=env.commandId,
            error=f"unknown commandType: {env.commandType}",
        ).model_dump()

    # 写配置（UpdateConfig）受 actor 白名单限制（user / desktop）；
    # 读配置（GetConfig）返回掩码，不对 actor 限制。
    if env.commandType == "UpdateConfig":
        actor_type = (env.actor or {}).get("type")
        if actor_type not in _ALLOWED_CONFIG_ACTORS:
            return CommandResult(
                ok=False, commandId=env.commandId,
                error=f"FORBIDDEN_ACTOR: write config (UpdateConfig) requires actor in "
                f"{_ALLOWED_CONFIG_ACTORS}, got {actor_type!r}",
            ).model_dump()

    handler = importlib.import_module(module_path).handle
    try:
        result: CommandResult = await handler(env, deps)
    except DispatchError as e:
        return CommandResult(
            ok=False, commandId=env.commandId, error=f"{e.code}: {e.message}"
        ).model_dump()
    except Exception as exc:
        # 兜底：任何未预期异常都转为干净的 ok=False，避免击垮 RPC 循环
        return CommandResult(
            ok=False, commandId=env.commandId, error=f"internal: {exc}"
        ).model_dump()
    return result.model_dump()
