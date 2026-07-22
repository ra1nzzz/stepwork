"""命令总线（W3-W4 Batch 0）。

路由规则（懒加载 handler 模块，规避 ``bus`` ↔ ``handlers`` 循环依赖）：

- ``ImportSource``   → ``worker.runtime.handlers.import_source``
- ``TranscribeSource``→ ``worker.runtime.handlers.transcribe_source``
- ``AnalyzeSource``  → ``worker.runtime.handlers.analyze_source``
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
}


class DispatchError(Exception):
    """handler 内抛出的领域错误（转为 CommandResult.ok=False）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def dispatch(raw: dict[str, Any], deps: Any) -> dict[str, Any]:
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

    handler = importlib.import_module(module_path).handle
    try:
        result: CommandResult = handler(env, deps)
    except DispatchError as e:
        return CommandResult(
            ok=False, commandId=env.commandId, error=f"{e.code}: {e.message}"
        ).model_dump()
    return result.model_dump()
