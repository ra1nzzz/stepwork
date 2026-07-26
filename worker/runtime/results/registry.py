"""commandType → detail 模型的注册表，以及 dispatch 出口的校验。

校验必须**真的跑**，否则契约就是装饰。分界：

- 生产：不匹配记 error 日志并放行 —— 契约问题不该在用户机器上制造新的失败；
- 测试：``strict_results()`` 里直接抛 :class:`ResultContractError`，当场变红。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from worker.runtime.results import models as m

logger = logging.getLogger("worker.runtime")


class ResultContractError(AssertionError):
    """handler 返回的 detail 与登记的契约不符（仅严格模式下抛出）。"""


#: commandType → detail 模型。
#:
#: **不要求全覆盖**：未登记的命令按老样子放行，这样契约可以按域分批推进，
#: 而不必一次写完 80 多条。哪些域必须有契约由 ``test_result_contracts`` 锁定。
RESULT_MODELS: dict[str, type[m.ResultModel]] = {
    # ---- publish 域 ----
    "CreatePlatformVariant": m.PlatformVariantDetail,
    "ListPlatformVariants": m.ListPlatformVariantsDetail,
    "ExportBundle": m.ExportBundleDetail,
    "BuildPlatformFillPackage": m.BuildPlatformFillPackageDetail,
    "RequestPublishAuthorization": m.RequestPublishAuthorizationDetail,
    "RecordPublishResult": m.RecordPublishResultDetail,
    "ListPublishJobs": m.ListPublishJobsDetail,
    "SchedulePublish": m.SchedulePublishDetail,
    "ListScheduledPublishes": m.ListScheduledPublishesDetail,
    "CancelScheduledPublish": m.CancelScheduledPublishDetail,
    "FireDueSchedules": m.FireDueSchedulesDetail,
    # ---- agent 域：只读与连接管理 ----
    "ListAgentTasks": m.ListAgentTasksDetail,
    "ListAgentArtifacts": m.ListAgentArtifactsDetail,
    "GetAgentTask": m.GetAgentTaskDetail,
    "ListAgentConnections": m.ListAgentConnectionsDetail,
    "SetAgentConnectionStatus": m.SetAgentConnectionStatusDetail,
    "DeleteAgentConnection": m.DeleteAgentConnectionDetail,
    # ---- agent 域：出站 MCP ----
    "AddMcpServer": m.AddMcpServerDetail,
    "ListMcpTools": m.ListMcpToolsDetail,
    "CallMcpTool": m.CallMcpToolDetail,
    # ---- agent 域：A2A ----
    "GetAgentCard": m.GetAgentCardDetail,
    "StartA2aServer": m.StartA2aServerDetail,
    "StopA2aServer": m.StopA2aServerDetail,
    "GetA2aServerStatus": m.GetA2aServerStatusDetail,
    "AddA2aAgent": m.AddA2aAgentDetail,
    "CallA2aSkill": m.CallA2aSkillDetail,
    # ---- agent 域：ACP ----
    "AddAcpAgent": m.AddAcpAgentDetail,
    "StartAcpSession": m.StartAcpSessionDetail,
    "SendAcpPrompt": m.SendAcpPromptDetail,
    "EndAcpSession": m.EndAcpSessionDetail,
    "ListAcpSessions": m.ListAcpSessionsDetail,
    # ---- render 域 ----
    "ExportEditTimeline": m.ExportEditTimelineDetail,
}

#: 严格模式开关。测试用 :func:`strict_results` 打开。
_STRICT = False


@contextmanager
def strict_results(enabled: bool = True) -> Iterator[None]:
    """切换严格模式。

    Args:
        enabled: True = 契约不符即抛错（测试默认）；False = 退回生产行为
            （只记日志），用于验证生产路径本身。
    """
    global _STRICT
    previous = _STRICT
    _STRICT = enabled
    try:
        yield
    finally:
        _STRICT = previous


def result_model_for(command_type: str) -> type[m.ResultModel] | None:
    return RESULT_MODELS.get(command_type)


def validate_detail(command_type: str, detail: dict[str, Any] | None) -> None:
    """校验 handler 产出的 detail。

    只在命令**成功**时校验：失败路径的 detail 是诊断信息（如 stderr 片段），
    形状本就自由，强行套契约只会逼出一堆无意义的可选字段。
    """
    model = RESULT_MODELS.get(command_type)
    if model is None:
        return
    try:
        model.model_validate(detail or {})
    except ValidationError as e:
        message = (
            f"{command_type} 的 detail 与契约不符："
            f"{e.error_count()} 处问题 —— {e.errors()[:3]}"
        )
        if _STRICT:
            raise ResultContractError(message) from e
        # 生产：只记日志。契约漂移不该在用户机器上变成新的失败模式。
        logger.error("result contract violation: %s", message)


def export_json_schemas(out_dir: Path) -> list[Path]:
    """把所有 detail 模型导成 JSON Schema（供前端代码生成与跨语言消费）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for command_type, model in sorted(RESULT_MODELS.items()):
        schema = model.model_json_schema()
        schema["title"] = f"{command_type}Detail"
        target = out_dir / f"{command_type}.schema.json"
        target.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        written.append(target)
    return written
