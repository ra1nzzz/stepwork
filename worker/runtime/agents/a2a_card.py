"""A2A Agent Card 与 Skill 映射（PRD-AGT-005 的契约面）。

SYSTEM_SPEC §13.5 定下了映射关系：

    Agent Card → AgentConnection + AgentCapability
    A2A Task   → AgentTask
    Artifact   → AgentArtifact
    Context    → AgentSession / Correlation ID

以及首批 6 个 Skill。本模块把这 6 个 Skill 落成**具体的 Command Bus
命令映射** —— Skill 不是自由文本，每个都对应一条已存在的命令，A2A 请求
最终仍走同一条总线，因此默认拒绝清单、审批降级、审计留痕全部自动生效，
不需要 A2A 这一层自建一套权限。

安全约束（SYSTEM_SPEC §13.5）：**A2A Server 默认不暴露 Publisher
Execute**。这里用白名单而不是黑名单来保证 —— 只有 SKILLS 里列出的
Skill 可达，发布类命令根本不在表里。
"""

from __future__ import annotations

from typing import Any

#: 我们自报的 Agent 名称与版本
AGENT_NAME = "STEPWORK"
AGENT_VERSION = "0.1.0"

#: A2A 协议版本
PROTOCOL_VERSION = "0.2.0"

#: Agent Card 的标准发现路径
CARD_PATH = "/.well-known/agent.json"


class Skill:
    """一个 A2A Skill 及其对应的 Command Bus 命令。"""

    def __init__(
        self,
        skill_id: str,
        name: str,
        description: str,
        command: str,
        *,
        tags: list[str] | None = None,
    ) -> None:
        self.id = skill_id
        self.name = name
        self.description = description
        self.command = command
        self.tags = tags or []

    def to_card_entry(self) -> dict[str, Any]:
        """转成 Agent Card 里的 skill 条目（不暴露内部命令名）。"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "inputModes": ["text/plain", "application/json"],
            "outputModes": ["application/json"],
        }


#: 首批 Skill（SYSTEM_SPEC §13.5）。顺序即 Agent Card 中的展示顺序。
#:
#: 注意 publish-preparation 映射到 ``BuildPlatformFillPackage`` 而**不是**
#: 任何执行发布的命令：ADR-008 规定 Publisher 只做「填充并预览」，A2A
#: 更不该成为绕过人工确认的后门。
SKILLS: tuple[Skill, ...] = (
    Skill(
        "content-reference-analysis",
        "参考内容分析",
        "分析参考素材，输出结构、卖点与可复用要素。",
        "AnalyzeSource",
        tags=["analysis"],
    ),
    Skill(
        "original-topic-proposal",
        "原创选题提案",
        "基于素材与账号画像生成差异化选题角度。",
        "GenerateTopic",
        tags=["ideation"],
    ),
    Skill(
        "script-drafting",
        "脚本初稿",
        "根据选题与账号画像生成脚本初稿。",
        "GenerateScript",
        tags=["writing"],
    ),
    Skill(
        "brand-voice-rewriting",
        "账号口吻改写",
        "按 BrandProfile 的语气与禁忌改写指定段落。",
        "EditParagraph",
        tags=["writing", "brand"],
    ),
    Skill(
        "media-draft-rendering",
        "视频草稿渲染",
        "把脚本渲染为可预览的视频草稿。",
        "CreateRenderJob",
        tags=["render"],
    ),
    Skill(
        "publish-preparation",
        "发布准备",
        "生成平台填充包（标题/正文/标签/素材路径）。不执行发布。",
        "BuildPlatformFillPackage",
        tags=["publish"],
    ),
)

#: skill id → Command Bus 命令名。白名单：表外的一律不可达。
SKILL_COMMANDS: dict[str, str] = {s.id: s.command for s in SKILLS}


def build_agent_card(base_url: str) -> dict[str, Any]:
    """构造 STEPWORK 自己的 Agent Card。

    Args:
        base_url: 对外可达的根地址（如 ``http://127.0.0.1:8788``）。

    ``capabilities.streaming`` 报 False —— 当前 A2A 端只做同步
    ``message/send``，谎报流式会让对端一直等 SSE。
    """
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": AGENT_NAME,
        "description": "本地优先的 AI 内容运营工作台：分析、选题、脚本、渲染与发布准备。",
        "url": base_url.rstrip("/"),
        "version": AGENT_VERSION,
        "provider": {"organization": "STEPWORK"},
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [s.to_card_entry() for s in SKILLS],
    }


def resolve_skill_command(skill_id: str) -> str | None:
    """skill id → 命令名；未登记的 Skill 返回 None（调用方应拒绝）。"""
    return SKILL_COMMANDS.get(skill_id)


def parse_remote_card(card: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """解析对方的 Agent Card → ``(名称, skill 列表)``。

    对方的 Card 是**不可信输入**：字段可能缺失、类型可能不对，甚至是
    恶意构造。这里只提取需要的字段并强制转型，不整体信任。
    """
    name = str(card.get("name") or "unknown-agent")
    skills: list[dict[str, Any]] = []
    for item in card.get("skills") or []:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("id") or "").strip()
        if not skill_id:
            continue
        skills.append(
            {
                "id": skill_id,
                "name": str(item.get("name") or skill_id),
                "description": str(item.get("description") or ""),
            }
        )
    return name, skills
