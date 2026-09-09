"""领域模型（W3-W4 Batch 0）。

全部基于 pydantic v2。JSON 列（``metadata`` / ``payload`` / ``producer`` /
``result_artifact_ids``）以 ``dict``/``list`` 形式存在，落库时由对应 repo
序列化为 TEXT。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


def _now() -> str:
    """当前 UTC ISO-8601 时间戳。"""
    return datetime.now(UTC).isoformat()


def _uid(prefix: str) -> str:
    """生成 ``<prefix>_<uuid4 hex>`` 形式的主键。"""
    return f"{prefix}_{uuid.uuid4().hex}"


class JobState(StrEnum):
    """任务主状态（SYSTEM_SPEC §10.1，9 主状态精简为本枚举）。"""

    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CANCELLED_REQUESTED = "cancelled-requested"
    EXPIRED = "expired"


class JobStage(StrEnum):
    """业务阶段（SYSTEM_SPEC §10.1，9 阶段）。"""

    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    DELEGATING = "delegating"
    GENERATING = "generating"
    PROPOSING = "proposing"
    SCRIPTING = "scripting"
    SYNTHESIZING = "synthesizing"
    #: S2 配图阶段（生图 provider）
    ILLUSTRATING = "illustrating"
    RENDERING = "rendering"
    PUBLISHING = "publishing"
    VERIFYING = "verifying"


class Workspace(BaseModel):
    """工作区（STEPWORK_HOME/workspaces/<id>）。"""

    id: str = Field(default_factory=lambda: _uid("ws"))
    name: str
    root_path: str
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    archived_at: str | None = None


class ContentProject(BaseModel):
    """内容项目。"""

    id: str = Field(default_factory=lambda: _uid("prj"))
    workspace_id: str
    title: str
    status: str = "active"
    brand_profile_id: str | None = None
    current_content_version_id: str | None = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class SourceAsset(BaseModel):
    """源素材（导入后落 ``source_assets`` 表）。"""

    id: str = Field(default_factory=lambda: _uid("asset"))
    project_id: str
    kind: str
    local_uri: str
    original_uri: str | None = None
    content_hash: str
    rights_declaration: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)

    def metadata_json(self) -> str:
        """序列化为落库 TEXT。"""
        return json.dumps(self.metadata, ensure_ascii=False)


class Job(BaseModel):
    """异步任务（``jobs`` 表）。"""

    id: str = Field(default_factory=lambda: _uid("job"))
    job_type: str
    state: JobState = JobState.PENDING
    stage: JobStage | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    progress: float = 0.0
    attempt_count: int = 0
    max_attempts: int = 3
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    heartbeat_at: str | None = None
    error_code: str | None = None
    result_artifact_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    def payload_json(self) -> str:
        """序列化为落库 TEXT。"""
        return json.dumps(self.payload, ensure_ascii=False)

    def result_json(self) -> str:
        """``result_artifact_ids`` 落库 TEXT。"""
        return json.dumps(self.result_artifact_ids, ensure_ascii=False)


class ContentVersion(BaseModel):
    """内容版本（脚本/分析稿等）。"""

    id: str = Field(default_factory=lambda: _uid("cv"))
    project_id: str
    parent_version_id: str | None = None
    content_type: str
    content: str
    content_hash: str
    producer: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)

    def producer_json(self) -> str:
        """序列化为落库 TEXT。"""
        return json.dumps(self.producer, ensure_ascii=False)


class ArtifactEnvelope(BaseModel):
    """产物信封（SYSTEM_SPEC §9，分析/渲染结果统一包装）。"""

    id: str = Field(default_factory=lambda: _uid("art"))
    kind: str
    ref_uri: str
    producer: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)


class TTSEngine(StrEnum):
    """TTS 旁白生成方式（SYSTEM_SPEC W6）。"""

    SYNTHESIZE = "synthesize"
    USER_AUDIO = "user_audio"


class RenderScene(BaseModel):
    """渲染用的一幕（由 handler 从 ``video_scenes`` 组装后挂到 ``RenderSpec``）。

    为什么不是让渲染器自己去查库：Provider 必须是**无状态、不碰 DB** 的
    （换一个渲染器实现不需要连数据库）。组装是 handler 的活。

    ``id`` 带上 ``video_scenes`` 行 id，是为了把渲染器实测到的
    ``born_at_sec`` 回填到对应的幕（见 :attr:`RenderResult.scene_born_sec`）。
    """

    #: ``video_scenes.id``；纯内存构造（如测试）时为 ``None``
    id: str | None = None
    seq: int
    text: str = ""
    highlight: str | None = None
    start_sec: float = 0.0
    duration_sec: float = 0.0
    image_uri: str | None = None
    emotion: str | None = None


class RenderSpec(BaseModel):
    """渲染规格（W6 RenderJob 输入）。"""

    source_version_id: str
    template: str = "vertical-caption-v1"
    tts_engine: TTSEngine = TTSEngine.SYNTHESIZE
    tts_provider: str | None = None
    user_audio_uri: str | None = None
    background_uri: str | None = None
    caption_text: str | None = None
    resolution: tuple[int, int] = (1080, 1920)
    fps: int = 30
    # S1 起：版式风格 / 美术风格 / 配图集（风格层选型用，S3 才真正消费）。
    # 三个字段全部带默认值——既有调用方（FFmpegRenderer、CreateRenderJob
    # 的既有 payload）不受影响，无需改表、无需改前端。
    #: 版式风格 id（如 ``ink_text`` 纸墨文字版 / ``illustration`` 插画版）
    style_id: str = "illustration"
    #: 美术风格（与版式正交：换美术风格不用改模板）
    art_style: str = "xiaohei"
    #: 配图集 id；``None`` 表示该风格不需要配图（A 版零素材依赖）
    image_set_id: str | None = None
    #: 渲染文档（HTML 视觉稿）uri —— 逐帧渲染器的画面来源。
    #: 新增字段而非复用 ``background_uri``：后者原语义是「背景图」，
    #: 一个字段两种含义迟早出事（S1 曾临时复用，S3 起收敛到这里）。
    design_doc_uri: str | None = None
    #: 分幕（S2）。``None`` = 整段一条（旧行为，FFmpegRenderer 与无分幕的
    #: 版本走这条）。有值时渲染器按 ``start_sec`` / ``duration_sec`` 切幕，
    #: 并把 ``image_uri`` 交给视觉稿 —— 这才是「画面按幕切换」。
    scenes: list[RenderScene] | None = None


class VideoScene(BaseModel):
    """S2 分幕（``video_scenes`` 行）。

    整条流水线此前没有「幕」这个概念：脚本是一整块文本、配音只知道总时长、
    渲染只能糊纯色背景。本模型把「幕」变成一等事实——文案产出后落库，配音
    回填 ``audio_uri`` + 实测 ``duration_sec``，配图回填 ``image_uri``，
    渲染按 ``start_sec`` / ``duration_sec`` 驱动画面。

    两条**必须写成断言**的隐性规则（摘自已验证流水线，静默失效过）：

    - ``highlight`` 必须是 ``text`` 的子串，否则标红静默失效
    - ``duration_sec`` 是 TTS **实测**时长，不是估值（画面被音频驱动，
      反过来就是「带配音的 PPT」）
    """

    id: str = Field(default_factory=lambda: _uid("vs"))
    version_id: str
    seq: int
    text: str = ""
    emotion: str | None = None
    highlight: str | None = None
    audio_uri: str | None = None
    image_uri: str | None = None
    start_sec: float = 0.0
    duration_sec: float = 0.0
    #: 该幕首句的实际起始秒；抽帧目检撞切句瞬间时据此前移取样（S1 遗留项）
    born_at_sec: float | None = None
    created_at: str = Field(default_factory=_now)


class RenderResult(BaseModel):
    """渲染结果元数据。"""

    video_uri: str
    duration_seconds: float
    template: str
    tts_engine: str
    #: 各幕首句在**画面**上的实际出现秒（与 :attr:`RenderSpec.scenes` 同序）。
    #: 视觉稿若暴露 ``window.__getSentBorn(i)`` 则由渲染器实测填入；
    #: 否则为空列表 —— 「没测到」和「测到是 0」必须可分，所以用空列表而非 0 填充。
    #: 用途：抽帧目检撞切句瞬间会取到空字幕，据此前移取样点（S1 遗留项）。
    scene_born_sec: list[float] = Field(default_factory=list)


class VideoDraftMeta(BaseModel):
    """落库到 ``content_versions(content_type="video_draft")`` 的元数据。"""

    video_uri: str
    duration_seconds: float
    template: str
    tts_engine: str
    resolution: tuple[int, int]
    fps: int
    source_version_id: str
    # Tranche 2（PRD-REN-001/003）：字幕 sidecar 与旁白音频 artifact 登记
    subtitles_uri: str | None = None
    audio_uri: str | None = None
    producer: dict[str, Any] = Field(default_factory=dict)


class TopicAngle(BaseModel):
    """单一选题角度（W5 + PRD-SCR-001）。

    PRD-SCR-001 验收标准要求「每个角度包含受众、观点、差异与风险」：
    ``audience`` / ``stance`` / ``risks`` 为此补齐；``rationale`` 承担
    「差异」。三个新字段给默认值，兼容旧版本落库内容（回读不炸）。
    """
    id: str
    title: str
    rationale: str
    hook: str
    #: 目标受众（PRD-SCR-001）
    audience: str | None = None
    #: 核心观点/立场（PRD-SCR-001）
    stance: str | None = None
    #: 风险点（PRD-SCR-001）
    risks: list[str] = Field(default_factory=list)


class TopicProposal(BaseModel):
    """选题提案（W5，落 ``content_versions(content_type="topic_proposal")``）。"""
    angles: list[TopicAngle]


class TopicProposalSpec(BaseModel):
    """``GenerateTopic`` 命令输入（W5 + PRD-SCR-001/BRD-002）。"""
    source_version_id: str
    #: PRD-SCR-001「生成 3—5 个差异化角度」——超出区间即 INVALID_ARGUMENT
    count: int = Field(default=5, ge=3, le=5)
    provider: dict[str, Any] | None = None
    #: PRD-BRD-002「生成时可选择启用」：False 时即便项目已绑定品牌档也不注入
    use_brand_profile: bool = True


class ScriptSpec(BaseModel):
    """``GenerateScript`` 命令输入（W5 + PRD-BRD-002）。"""
    proposal_version_id: str | None = None
    topic_id: str | None = None
    outline: str | None = None
    style: str = "short_video"
    provider: dict[str, Any] | None = None
    #: PRD-BRD-002「生成时可选择启用」：False 时不注入品牌画像
    use_brand_profile: bool = True


class CommandEnvelope(BaseModel):
    """命令信封 v1（对应 schemas/command-envelope.schema.json）。"""

    commandId: str
    commandType: str
    schemaVersion: str = "1"
    actor: dict[str, Any]
    source: str
    workspaceId: str
    projectId: str | None = None
    idempotencyKey: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    requestedAt: str


class CommandResult(BaseModel):
    """Command Bus 的统一返回。"""

    ok: bool = True
    commandId: str | None = None
    job_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


# ===== 设置页配置（SET.5 / SET.7） =====
# 所有字段给默认值，避免旧库 / 缺字段的 payload 触发 KeyError。
class ConfigSpec(BaseModel):
    """``UpdateConfig`` 命令输入：前端完整 ``SettingsConfig``。

    密钥（``*Key`` / ``*Secret``）随 payload 一道进入 handler，但**绝不**
    落入 SQLite——handler 会剥离后写入 ``Workspace.settings``，仅把密钥推入
    进程内存的覆盖层（``resolve.CONFIG_OVERRIDES``）。
    """

    llm: dict[str, Any] = Field(default_factory=dict)
    asr: dict[str, Any] = Field(default_factory=dict)
    tts: dict[str, Any] = Field(default_factory=dict)
    workspace: dict[str, Any] = Field(default_factory=dict)
    brand: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    export: dict[str, Any] = Field(default_factory=dict)
    ui: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_sections(self) -> ConfigSpec:
        # 防御：每个非空 section 必须是对象，否则 payload 畸形，
        # 在 handler 内被转译为干净的 INVALID_ARGUMENT 错误。
        for name in ("llm", "asr", "tts", "workspace", "brand", "data", "export", "ui"):
            val = getattr(self, name)
            if val is not None and not isinstance(val, dict):
                raise ValueError(f"config section {name!r} must be an object")
        return self


class ConfigView(BaseModel):
    """``GetConfig`` 命令输出。

    - ``config``：合并后的完整配置，密钥一律掩码（``"••••"``），永不回显明文。
    - ``resolved``：解析摘要（provider / model / 是否持有密钥），供前端「检查配置」展示。
    """

    config: dict[str, Any] = Field(default_factory=dict)
    resolved: dict[str, Any] = Field(default_factory=dict)
