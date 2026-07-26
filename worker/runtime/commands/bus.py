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
from worker.runtime.models import CommandEnvelope, CommandResult

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
    # W8: 插件 / Provenance / Agent / 诊断包（Layer 0 路由先行，handler 由各支线补齐）
    "ListPlugins": "worker.runtime.handlers.plugins",
    "GetPluginManifest": "worker.runtime.handlers.plugins",
    "InstallPlugin": "worker.runtime.handlers.plugins",
    "EnablePlugin": "worker.runtime.handlers.plugins",
    "DisablePlugin": "worker.runtime.handlers.plugins",
    "GetProvenance": "worker.runtime.handlers.provenance",
    "ListAgentTasks": "worker.runtime.handlers.agent",
    "ListAgentArtifacts": "worker.runtime.handlers.agent",
    "GetAgentTask": "worker.runtime.handlers.agent",
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
    "SaveAnalysis": "worker.runtime.handlers.save_analysis",
    "ListContentVersions": "worker.runtime.handlers.queries",
    "GetContentVersion": "worker.runtime.handlers.queries",
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
# 映射 §9.1 七项（未列出的 = 系统中尚无对应能力）：
#   发布            → ExportBundle（写出导出包到磁盘）
#   删除项目或资产  → DeleteAsset / ArchiveWorkspace
#   覆盖原文件      → RestoreWorkspace / ImportProject（覆盖库与项目数据）
#   向外部上传原素材→ TranscribeSource（cloud ASR 会上传媒体本体）
#   安装插件        → InstallPlugin / EnablePlugin / DisablePlugin
#   读取平台凭据    → GetConfig 已是掩码视图，无需拉黑
#   使用高费用模型  → AnalyzeSource 按 PRD-AGT-002「外部 Agent 可发起分析」
#                     显式允许，费用由 audit 记录；预算上限属后续能力
#
# 注：UpdateConfig 另由 _ALLOWED_CONFIG_ACTORS 拦截（更严：仅 user/desktop）。
# ---------------------------------------------------------------------------
_AGENT_FORBIDDEN_COMMANDS: frozenset[str] = frozenset(
    {
        "ExportBundle",
        "DeleteAsset",
        "ArchiveWorkspace",
        "RestoreWorkspace",
        "ImportProject",
        "TranscribeSource",
        "InstallPlugin",
        "EnablePlugin",
        "DisablePlugin",
        # 手动清理会真实删除磁盘文件，归入「删除资产」类
        "RunCleanup",
    }
)

# 视为「外部 Agent」的调用方特征：actor.type 或 source 任一命中即算。
_AGENT_ACTOR_TYPES: frozenset[str] = frozenset({"agent"})
_AGENT_SOURCES: frozenset[str] = frozenset({"mcp", "a2a", "acp"})


def is_agent_caller(env: CommandEnvelope) -> bool:
    """判断信封是否来自外部 Agent（PRD §9.1 的约束对象）。"""
    actor_type = (env.actor or {}).get("type")
    return actor_type in _AGENT_ACTOR_TYPES or env.source in _AGENT_SOURCES


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

    # PRD §9.1：外部 Agent 默认不得直接执行发布/删除/覆盖/外传/装插件类命令。
    # 与 MCP 工具集边界互为冗余——即便某天工具被误注册，这里仍然拒绝。
    if env.commandType in _AGENT_FORBIDDEN_COMMANDS and is_agent_caller(env):
        return CommandResult(
            ok=False, commandId=env.commandId,
            error=(
                f"FORBIDDEN_ACTOR: {env.commandType} 属 PRD §9.1 高风险操作，"
                f"外部 Agent 不可直接执行，需由用户在桌面端确认后发起"
            ),
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
