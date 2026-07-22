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

from pydantic import BaseModel, Field


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


class RenderResult(BaseModel):
    """渲染结果元数据。"""

    video_uri: str
    duration_seconds: float
    template: str
    tts_engine: str


class VideoDraftMeta(BaseModel):
    """落库到 ``content_versions(content_type="video_draft")`` 的元数据。"""

    video_uri: str
    duration_seconds: float
    template: str
    tts_engine: str
    resolution: tuple[int, int]
    fps: int
    source_version_id: str
    producer: dict[str, Any] = Field(default_factory=dict)


class TopicAngle(BaseModel):
    """单一选题角度（W5）。"""
    id: str
    title: str
    rationale: str
    hook: str


class TopicProposal(BaseModel):
    """选题提案（W5，落 ``content_versions(content_type="topic_proposal")``）。"""
    angles: list[TopicAngle]


class TopicProposalSpec(BaseModel):
    """``GenerateTopic`` 命令输入（W5）。"""
    source_version_id: str
    count: int = 5
    provider: dict[str, Any] | None = None


class ScriptSpec(BaseModel):
    """``GenerateScript`` 命令输入（W5）。"""
    proposal_version_id: str | None = None
    topic_id: str | None = None
    outline: str | None = None
    style: str = "short_video"
    provider: dict[str, Any] | None = None


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
