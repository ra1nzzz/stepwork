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

import asyncio
import importlib
import logging
from typing import Any

from worker.runtime.commands import idempotency
from worker.runtime.commands.envelope import EnvelopeError, parse_envelope
from worker.runtime.models import CommandEnvelope, CommandResult
from worker.runtime.observability import (
    CommandTimer,
    record_metric,
    resolve_correlation_id,
)
from worker.runtime.results import validate_detail

logger = logging.getLogger("worker.runtime")

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
    # PRD-SCR-003：段落级生成/重写/扩写/压缩
    "EditParagraph": "worker.runtime.handlers.edit_paragraph",
    "GetConfig": "worker.runtime.handlers.config",
    "UpdateConfig": "worker.runtime.handlers.config",
    "ListProjects": "worker.runtime.handlers.queries",
    "GetProject": "worker.runtime.handlers.queries",
    "GetJobStatus": "worker.runtime.handlers.queries",
    "ListJobs": "worker.runtime.handlers.queries",
    "CreateProject": "worker.runtime.handlers.projects",
    "DeleteAsset": "worker.runtime.handlers.projects",
    # PRD-WS-003：项目标签
    "SetProjectTags": "worker.runtime.handlers.projects",
    # W8: 插件 / Provenance / Agent / 诊断包（Layer 0 路由先行，handler 由各支线补齐）
    "ListPlugins": "worker.runtime.handlers.plugins",
    "GetPluginManifest": "worker.runtime.handlers.plugins",
    # PRD-PLG-002：安装前预览权限（只读，不写库）
    "PreviewPluginManifest": "worker.runtime.handlers.plugins",
    "InstallPlugin": "worker.runtime.handlers.plugins",
    "EnablePlugin": "worker.runtime.handlers.plugins",
    "DisablePlugin": "worker.runtime.handlers.plugins",
    # PRD-PLG-003 卸载 / PRD-PLG-005 健康检查
    "UninstallPlugin": "worker.runtime.handlers.plugins",
    "CheckPluginHealth": "worker.runtime.handlers.plugins",
    "GetProvenance": "worker.runtime.handlers.provenance",
    "ListAgentTasks": "worker.runtime.handlers.agent",
    "ListAgentArtifacts": "worker.runtime.handlers.agent",
    "GetAgentTask": "worker.runtime.handlers.agent",
    # PRD-AGT-007：Agent 连接管理（启停 / 删除）
    "ListAgentConnections": "worker.runtime.handlers.agent",
    "SetAgentConnectionStatus": "worker.runtime.handlers.agent",
    "DeleteAgentConnection": "worker.runtime.handlers.agent",
    # PRD-AGT-004 出站 MCP 客户端（我们去连别人的 Server）
    "AddMcpServer": "worker.runtime.handlers.mcp_client",
    "ListMcpTools": "worker.runtime.handlers.mcp_client",
    "CallMcpTool": "worker.runtime.handlers.mcp_client",
    # PRD-AGT-005 A2A：入站 Server 开关 + 出站 Client
    "GetAgentCard": "worker.runtime.handlers.a2a",
    "StartA2aServer": "worker.runtime.handlers.a2a",
    "StopA2aServer": "worker.runtime.handlers.a2a",
    "GetA2aServerStatus": "worker.runtime.handlers.a2a",
    "AddA2aAgent": "worker.runtime.handlers.a2a",
    "CallA2aSkill": "worker.runtime.handlers.a2a",
    # PRD-AGT-006 ACP：本地 Agent 子进程会话
    "AddAcpAgent": "worker.runtime.handlers.acp",
    "StartAcpSession": "worker.runtime.handlers.acp",
    "SendAcpPrompt": "worker.runtime.handlers.acp",
    "EndAcpSession": "worker.runtime.handlers.acp",
    "ListAcpSessions": "worker.runtime.handlers.acp",
    # 定时发布（优先走平台原生定时；无原生能力则本地到点提醒）
    "SchedulePublish": "worker.runtime.handlers.publish",
    "ListScheduledPublishes": "worker.runtime.handlers.publish",
    "CancelScheduledPublish": "worker.runtime.handlers.publish",
    "FireDueSchedules": "worker.runtime.handlers.publish",
    # PRD-REN-006 导出第三方剪辑数据（OTIO / EDL）
    "ExportEditTimeline": "worker.runtime.handlers.export_timeline",
    "ExportDiagnosticsBundle": "worker.runtime.handlers.diagnostics",
    # W9: 集成 / 数据迁移 / 种子测试（Layer 0 路由先行，handler 由各支线补齐）
    "ExportProject": "worker.runtime.handlers.project_io",
    "ImportProject": "worker.runtime.handlers.project_io",
    "BackupWorkspace": "worker.runtime.handlers.backup",
    "RestoreWorkspace": "worker.runtime.handlers.backup",
    # Tranche 2: BrandProfile / 分析保存 / 版本查询 / Workspace / 发布 MVP
    "CreateBrandProfile": "worker.runtime.handlers.brand",
    "UpdateBrandProfile": "worker.runtime.handlers.brand",
    "ListBrandProfiles": "worker.runtime.handlers.brand",
    "SetProjectBrandProfile": "worker.runtime.handlers.brand",
    # PRD-BRD-003 历史脚本（风格参考）/ PRD-BRD-004 偏好记录
    "ImportBrandScript": "worker.runtime.handlers.brand",
    "ListBrandScripts": "worker.runtime.handlers.brand",
    "DeleteBrandScript": "worker.runtime.handlers.brand",
    "RecordPreference": "worker.runtime.handlers.brand",
    "SaveAnalysis": "worker.runtime.handlers.save_analysis",
    "ListContentVersions": "worker.runtime.handlers.queries",
    "GetContentVersion": "worker.runtime.handlers.queries",
    # PRD-SCR-006：AI 初稿与最终稿比较
    "DiffContentVersions": "worker.runtime.handlers.queries",
    "ListRenderTemplates": "worker.runtime.handlers.queries",
    # PRD-SRC-003：素材可追溯（此前只有写入/删除，无任何读命令）
    "ListSourceAssets": "worker.runtime.handlers.queries",
    "GetSourceAsset": "worker.runtime.handlers.queries",
    # PRD-SRC-005 手动清理 / PRD-ANA-006 审计可查
    "RunCleanup": "worker.runtime.handlers.maintenance",
    "ListAuditEvents": "worker.runtime.handlers.maintenance",
    "CreateWorkspace": "worker.runtime.handlers.workspaces",
    "RenameWorkspace": "worker.runtime.handlers.workspaces",
    "ArchiveWorkspace": "worker.runtime.handlers.workspaces",
    "ListWorkspaces": "worker.runtime.handlers.workspaces",
    "CreatePlatformVariant": "worker.runtime.handlers.publish",
    "ListPlatformVariants": "worker.runtime.handlers.publish",
    "ExportBundle": "worker.runtime.handlers.publish",
    # PRD-PUB-004 一次性发布授权 / PRD-PUB-005 发布结果与证据
    "RequestPublishAuthorization": "worker.runtime.handlers.publish",
    "RecordPublishResult": "worker.runtime.handlers.publish",
    "ListPublishJobs": "worker.runtime.handlers.publish",
    # PRD-PUB-003：填充包（ADR-008 只填写+预览，绝不自动发布）
    "BuildPlatformFillPackage": "worker.runtime.handlers.publish",
    # PRD-AGT-008 / §9.1 §9.2：审批中心
    "CreateApprovalRequest": "worker.runtime.handlers.approvals",
    "ListApprovalRequests": "worker.runtime.handlers.approvals",
    "DecideApprovalRequest": "worker.runtime.handlers.approvals",
}

# 写配置（UpdateConfig）仅允许来自「用户态 / 桌面壳」的 actor（三角色 P0 安全模型）；
# 读配置（GetConfig）返回掩码视图（``••••`` + ``hasKey:bool``），无任何密钥外泄风险，
# 故对任何合法 actor 开放。MCP 不越权的根保证是「MCP 永不注册 UpdateConfig」（tool 集边界）。
_ALLOWED_CONFIG_ACTORS: tuple[str, ...] = ("user", "desktop")

# ---------------------------------------------------------------------------
# PRD §9.1「默认禁止外部 Agent 直接执行」——bus 层纵深防御。
#
# 第一层是「MCP 只注册只读工具」（tool 集边界）；但那层依赖「永远别注册错」，
# 一旦未来 MCP 加工具、或 A2A/ACP 适配器（source enum 已预留 a2a/acp）复用
# 同一条 dispatch，写命令会立刻可达且无告警。故在此再设一道按 commandType
# 的黑名单，对 agent 类调用方一律拒绝，与工具集是否注册无关。
#
# 采用**默认拒绝的允许清单**而非黑名单：PRD 该节标题就是「默认禁止」，
# 且黑名单有结构性缺陷 —— 每加一个新写命令都得记得同步拉黑，漏一个就是
# 静默的权限放大（复核确实发现漏了 ExportProject / BackupWorkspace /
# ImportSource / SaveScript / EditParagraph / CreateRenderJob 等）。
# 允许清单则相反：新命令**默认就是禁止的**，要放开必须显式加进来。
#
# 清单内容 = PRD-AGT-002 明示的「读取项目、发起分析和查询任务」：
# 全部只读查询 + AnalyzeSource。其余一律拒绝，包括计费的 GenerateTopic /
# GenerateScript / CreateRenderJob 与所有写命令。
#
# 注：UpdateConfig 另由 _ALLOWED_CONFIG_ACTORS 拦截（更严：仅 user/desktop）。
# ---------------------------------------------------------------------------
_AGENT_ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        # 读项目 / 查任务（PRD-AGT-002 明示的 MCP 能力）
        "ListProjects",
        "GetProject",
        "GetJobStatus",
        "ListJobs",
        "ListContentVersions",
        "GetContentVersion",
        "ListSourceAssets",
        "GetSourceAsset",
        "ListBrandProfiles",
        "ListWorkspaces",
        "ListPlatformVariants",
        "ListRenderTemplates",
        "ListAuditEvents",
        "GetProvenance",
        "ListAgentTasks",
        "ListAgentArtifacts",
        "GetAgentTask",
        "ListPlugins",
        "GetPluginManifest",
        "PreviewPluginManifest",  # 只读预览，不写库
        # 读配置：GetConfig 返回掩码视图（``••••`` + hasKey），无密钥外泄
        "GetConfig",
        # 发起分析：PRD-AGT-002 唯一显式豁免的写命令（费用由 audit 记录）
        "AnalyzeSource",
        # 允许外部 Agent **申请**审批（§9.1 的降级路径），
        # 但 DecideApprovalRequest 不在清单内——绝不能自批自用
        "CreateApprovalRequest",
        "ListApprovalRequests",
    }
)

# 视为「外部 Agent」的调用方特征：actor.type 或 source 任一命中即算。
# plugin 一并纳入：插件适配器若复用同一条 dispatch，同样不该直达写命令。
#: 单个 actor 的待审批请求上限（防止外部 agent 无限灌库）
_MAX_PENDING_APPROVALS_PER_ACTOR = 50

_AGENT_ACTOR_TYPES: frozenset[str] = frozenset({"agent", "plugin"})
_AGENT_SOURCES: frozenset[str] = frozenset({"mcp", "a2a", "acp", "plugin"})


def is_agent_caller(env: CommandEnvelope) -> bool:
    """判断信封是否来自外部 Agent（PRD §9.1 的约束对象）。"""
    actor_type = (env.actor or {}).get("type")
    return actor_type in _AGENT_ACTOR_TYPES or env.source in _AGENT_SOURCES


def _connection_disabled(env: CommandEnvelope, deps: Any) -> bool:
    """该协议通道是否已被用户停用（PRD-AGT-007）。

    连接行不存在时视为**未停用** —— 首次调用时行尚未创建（由
    ``agent_record.ensure_connection`` 在成功后补建），不能因此拒绝。
    """
    conn = getattr(getattr(deps, "repos", None), "conn", None)
    if conn is None:
        return False
    try:
        row = conn.execute(
            "SELECT status FROM agent_connections WHERE id=?",
            (f"conn_{env.source}",),
        ).fetchone()
    except Exception:  # noqa: BLE001 - 查询失败不改变默认放行语义
        return False
    return row is not None and str(row["status"]) == "inactive"


def _create_preparation_task(env: CommandEnvelope, deps: Any) -> str | None:
    """把被拒的 agent 请求降级为待审批准备任务（§9.1）；失败返回 None。

    登记失败绝不改变「拒绝」这个结果本身 —— 安全语义不依赖审批表可写。
    """
    conn = getattr(getattr(deps, "repos", None), "conn", None)
    if conn is None:
        return None
    try:
        from worker.runtime.handlers.approvals import (
            STATUS_PENDING,
            create_request,
        )

        actor = env.actor or {}
        actor_id = f"{actor.get('type', 'agent')}:{actor.get('id', 'unknown')}"
        target = env.projectId or env.workspaceId

        # 去重：同一 actor 对同一 (命令, 目标) 已有待审批请求就复用，
        # 否则 agent 循环调禁用命令即可无限灌满 approval_requests（本地 DoS，
        # 且每条都全量落 payload）。
        existing = conn.execute(
            "SELECT id FROM approval_requests "
            "WHERE actor=? AND action_type=? AND target=? AND status=? "
            "ORDER BY created_at DESC LIMIT 1",
            (actor_id, env.commandType, target, STATUS_PENDING),
        ).fetchone()
        if existing is not None:
            return str(existing["id"])

        # 限流：单个 actor 的待审批数量上限，防止用不同 target 绕过去重
        pending_count = conn.execute(
            "SELECT COUNT(*) n FROM approval_requests WHERE actor=? AND status=?",
            (actor_id, STATUS_PENDING),
        ).fetchone()["n"]
        if pending_count >= _MAX_PENDING_APPROVALS_PER_ACTOR:
            logger.warning(
                "actor %s reached pending approval cap (%s); dropping request for %s",
                actor_id, _MAX_PENDING_APPROVALS_PER_ACTOR, env.commandType,
            )
            return None

        return create_request(
            conn,
            actor=actor_id,
            action_type=env.commandType,
            target=target,
            risk_summary=(
                f"外部 {env.source} 调用方请求执行 {env.commandType}，"
                f"该操作按 PRD §9.1 需用户确认"
            ),
            payload=dict(env.payload or {}),
        )
    except Exception:  # noqa: BLE001 - 准备任务登记失败不影响拒绝语义
        logger.exception("preparation task creation failed for %s", env.commandType)
        return None


class DispatchError(Exception):
    """handler 内抛出的领域错误（转为 CommandResult.ok=False）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


async def dispatch(raw: dict[str, Any], deps: Any) -> dict[str, Any]:
    """校验信封并路由到对应 handler，全程计时与留痕。

    可观测性包在最外层而不是各 handler 里：埋在 handler 里必然会漏，
    而漏掉的那条恰好就是出问题时最想看的那条。信封解析失败也要记 ——
    「命令根本没进来」和「命令执行失败」是两回事，日志里必须能分开。

    Args:
        raw: 调用方传入的原始命令 dict。
        deps: 注入依赖（``Deps``：repos / ingest / asr / ai）。

    Returns:
        :class:`CommandResult` 的 ``model_dump()`` 字典。
    """
    command_type = str(raw.get("commandType") or "?")
    correlation_id = resolve_correlation_id(raw)
    with CommandTimer(command_type, correlation_id, raw.get("payload")) as timer:
        result = await _dispatch_inner(raw, deps)
        timer.finish(ok=bool(result.get("ok")), error=result.get("error"))
        conn = getattr(getattr(deps, "repos", None), "conn", None)
        if conn is not None:
            record_metric(conn, timer)
        return result


async def _dispatch_inner(raw: dict[str, Any], deps: Any) -> dict[str, Any]:
    """实际的校验与路由（计时与指标由 :func:`dispatch` 负责）。"""
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

    # PRD-AGT-007「可启停连接」：停用的通道一律拒绝，否则「停用」只是
    # UI 上的装饰。放在允许清单校验之前——停用意味着连读也不给。
    if is_agent_caller(env) and _connection_disabled(env, deps):
        return CommandResult(
            ok=False, commandId=env.commandId,
            error=(
                f"FORBIDDEN_ACTOR: 协议通道 {env.source} 已被停用，"
                f"请在「Agent 连接」页重新启用"
            ),
        ).model_dump()

    # PRD §9.1：外部 Agent 默认禁止直接执行，只放行允许清单内的命令。
    # 与 MCP 工具集边界互为冗余——即便某天工具被误注册，这里仍然拒绝。
    if is_agent_caller(env) and env.commandType not in _AGENT_ALLOWED_COMMANDS:
        # §9.1 原文是「以下操作默认**只能创建准备任务**」——只拒绝等于
        # 「无路可走」，不符合 PRD。这里把被拒的请求降级成一条待审批的
        # 准备任务，用户可在审批中心批准后自行发起。
        approval_id = _create_preparation_task(env, deps)
        return CommandResult(
            ok=False, commandId=env.commandId,
            error=(
                f"FORBIDDEN_ACTOR: {env.commandType} 不在外部 Agent 允许清单内"
                f"（PRD §9.1 默认禁止直接执行），已创建待审批的准备任务"
            ),
            detail={"approval_id": approval_id} if approval_id else {},
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

    # PRD §13「重复任务幂等阻止重复输出」：同一 idempotencyKey 已成功执行过
    # 就直接返回上次结果，不再重复产出内容版本、不再重复计费。
    conn = getattr(getattr(deps, "repos", None), "conn", None)
    cached = idempotency.lookup(conn, env)
    if cached is not None:
        return cached

    handler = importlib.import_module(module_path).handle
    try:
        result: CommandResult = await handler(env, deps)
    except asyncio.CancelledError:
        # 取消。必须在此边界转成正常结果：CancelledError 是 BaseException，
        # 若继续向上抛，RPC 循环不会写回任何响应帧，调用方只能一直等到超时
        # （Rust 侧 30 分钟）。job 终态已由 content_job 落为 CANCELLED。
        #
        # 区分两种取消：CancelJob 会先把 job 登记为「用户请求取消」；
        # worker 关停时批量 cancel in-flight 任务则没有这个登记，若一律
        # 报「已被用户取消」会误导用户。
        from worker.runtime.jobs.cancel import was_user_cancelled

        reason = (
            "任务已被用户取消"
            if was_user_cancelled(asyncio.current_task())
            else "任务因 worker 关停而中止"
        )
        return CommandResult(
            ok=False, commandId=env.commandId, error=f"CANCELLED: {reason}"
        ).model_dump()
    except DispatchError as e:
        return CommandResult(
            ok=False, commandId=env.commandId, error=f"{e.code}: {e.message}"
        ).model_dump()
    except Exception as exc:
        # 兜底：任何未预期异常都转为干净的 ok=False，避免击垮 RPC 循环
        return CommandResult(
            ok=False, commandId=env.commandId, error=f"internal: {exc}"
        ).model_dump()

    # PRD-AGT-003：外部 Agent 的产出必须带来源与信任等级。集中在此登记，
    # 避免逐个 handler 埋点漏掉；登记失败不影响业务结果。
    if is_agent_caller(env) and conn is not None:
        from worker.runtime.agent_record import record_agent_activity

        record_agent_activity(
            conn, env, artifact_ids=list(result.artifact_ids), ok=result.ok
        )

    # 响应契约（worker/runtime/results）：handler 产出的 detail 必须符合登记的
    # 形状。放在 dispatch 出口而不是各 handler 里，是为了「登记了就一定被校验」
    # —— 逐个 handler 埋点必然会漏，而漏掉的那条恰好就是会静默漂移的那条。
    # 只校验成功路径：失败 detail 是诊断信息，形状本就自由。
    if result.ok:
        validate_detail(env.commandType, result.detail)

    dumped: dict[str, Any] = result.model_dump()
    # 只缓存成功结果：失败若被缓存，一次网络抖动就会把同一个 key 永久钉死
    idempotency.remember(conn, env, dumped)
    return dumped
