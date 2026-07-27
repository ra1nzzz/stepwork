"""各命令 ``detail`` 的 pydantic 模型（publish / agent 两域先行）。

写法约定：

- **照实写**，不照想象写。字段名与顺序一律以 handler 现有产出为准 ——
  契约的作用是锁住现状不漂移，不是趁机改接口。
- 一律 ``extra="forbid"``：多返回字段和少返回字段一样危险（前端会当它不存在，
  或者悄悄依赖上一个没进契约的字段）。
- 可选字段显式给 ``None`` 默认值，而不是靠 ``total=False`` 之类的隐式约定。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ResultModel(BaseModel):
    """所有 detail 模型的基类：禁止多余字段。"""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# publish 域
# ---------------------------------------------------------------------------


class PlatformVariantDetail(ResultModel):
    variant: dict[str, Any]


class ListPlatformVariantsDetail(ResultModel):
    variants: list[dict[str, Any]]


class ExportBundleDetail(ResultModel):
    bundle_path: str
    variant_id: str
    #: 文件名映射（video/cover/title/body/tags），缺失项为 None
    files: dict[str, str | None]


class BuildPlatformFillPackageDetail(ResultModel):
    fill_package: dict[str, Any]


class RequestPublishAuthorizationDetail(ResultModel):
    approval_id: str
    publish_job_id: str
    content_hash: str
    state: str


class RecordPublishResultDetail(ResultModel):
    publish_job: dict[str, Any]


class ListPublishJobsDetail(ResultModel):
    publish_jobs: list[dict[str, Any]]


class SchedulePublishDetail(ResultModel):
    id: str
    mode: str
    scheduled_at: str
    platform: str
    platform_label: str
    #: 只有平台原生定时才是真无人值守；本地提醒模式为 False
    unattended: bool
    note: str
    issues: list[dict[str, Any]]
    status: str
    mode_description: str


class ListScheduledPublishesDetail(ResultModel):
    scheduled: list[dict[str, Any]]


class CancelScheduledPublishDetail(ResultModel):
    cancelled: str


class FireDueSchedulesDetail(ResultModel):
    fired: list[dict[str, Any]]
    count: int


# ---------------------------------------------------------------------------
# agent 域（只读列表 + 连接管理）
# ---------------------------------------------------------------------------


class ListAgentTasksDetail(ResultModel):
    tasks: list[dict[str, Any]]
    note: str


class ListAgentArtifactsDetail(ResultModel):
    artifacts: list[dict[str, Any]]
    note: str


class GetAgentTaskDetail(ResultModel):
    task: dict[str, Any]


class ListAgentConnectionsDetail(ResultModel):
    connections: list[dict[str, Any]]


class SetAgentConnectionStatusDetail(ResultModel):
    connection: dict[str, Any]


class DeleteAgentConnectionDetail(ResultModel):
    deleted: str


# ---------------------------------------------------------------------------
# agent 域（出站 MCP）
# ---------------------------------------------------------------------------


class AddMcpServerDetail(ResultModel):
    connection_id: str
    name: str
    server_info: dict[str, Any]
    tools: list[dict[str, Any]]


class ListMcpToolsDetail(ResultModel):
    connection_id: str
    tools: list[dict[str, Any]]


class CallMcpToolDetail(ResultModel):
    agent_task_id: str
    tool: str
    text: str
    #: 外部内容一律 external-unverified / pending_review，UI 据此提示未复核
    trust_level: str
    review_state: str
    is_error: bool


# ---------------------------------------------------------------------------
# agent 域（A2A）
# ---------------------------------------------------------------------------


class GetAgentCardDetail(ResultModel):
    card: dict[str, Any]


class StartA2aServerDetail(ResultModel):
    running: bool
    url: str
    card_url: str
    #: 令牌只在启动时回一次且不落盘，UI 必须当场展示给用户
    token: str


class StopA2aServerDetail(ResultModel):
    stopped: bool
    running: bool


class GetA2aServerStatusDetail(ResultModel):
    running: bool
    url: str


class AddA2aAgentDetail(ResultModel):
    connection_id: str
    agent_name: str
    url: str
    skills: list[dict[str, Any]]
    #: 明确告知令牌未落盘，避免用户以为已保存
    token_persisted: bool


class CallA2aSkillDetail(ResultModel):
    agent_task_id: str
    skill: str
    text: str
    remote_state: str | None = None
    trust_level: str
    review_state: str


# ---------------------------------------------------------------------------
# agent 域（ACP）
# ---------------------------------------------------------------------------


class AddAcpAgentDetail(ResultModel):
    connection_id: str
    agent_info: dict[str, Any]


class StartAcpSessionDetail(ResultModel):
    session_id: str
    external_session_id: str
    #: Root/Scope，回报给用户确认 Agent 能看到什么（§13.6）
    root: str
    project_id: str


class SendAcpPromptDetail(ResultModel):
    agent_task_id: str
    stop_reason: str | None = None
    text: str
    updates: list[dict[str, Any]]
    #: 有待批项时 UI 要提示用户去审批中心
    pending_approvals: int
    trust_level: str
    review_state: str


class EndAcpSessionDetail(ResultModel):
    ended: bool


class ListAcpSessionsDetail(ResultModel):
    sessions: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# 渲染域：剪辑时间线导出
# ---------------------------------------------------------------------------


class ExportEditTimelineDetail(ResultModel):
    path: str
    format: str
    scene_count: int
    marker_count: int
    note: str
