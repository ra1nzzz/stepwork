"""``TranscribeSource`` 命令处理（W3，Batch 1）。

职责：
1. 解析素材（``asset_id`` 或 ``local_uri``）→ 解析目标 project
2. 创建转写 job（PENDING → RUNNING），获取租约（kill -9 恢复用）
3. 调用注入的 ASR Provider 转写
4. 字符上限保护后，将 transcript 作为 ``content_versions``（``transcript``）落库
5. job 标记 SUCCEEDED 并回写 artifact id，返回 :class:`CommandResult`
"""

from __future__ import annotations

import hashlib
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.jobs import content_job, persist_content_version
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
    JobStage,
)

# 落库前的字符上限保护（三角色头脑风暴 P0）
_MAX_TRANSCRIPT_CHARS = 20000


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``TranscribeSource``。"""
    repos = deps.repos
    p: dict[str, Any] = env.payload

    asr = deps.asr
    if asr is None:
        raise DispatchError("UNAVAILABLE", "asr provider not configured")

    asset_id = p.get("asset_id")
    local_uri = p.get("local_uri")
    if asset_id:
        asset = repos.source_assets.get(asset_id)
        if asset is None:
            raise DispatchError("NOT_FOUND", f"asset {asset_id} not found")
        local_uri = asset.local_uri
    if not local_uri:
        raise DispatchError("INVALID_ARGUMENT", "asset_id or local_uri required")

    async with content_job(
        repos,
        job_type="transcribe",
        stage=JobStage.TRANSCRIBING,
        env=env,
        fail_code="TRANSCRIBE_FAILED",
        lease="transcribe_source",
        payload={
            "asset_id": asset_id,
            "local_uri": local_uri,
            "provider": getattr(asr, "name", "unknown"),
        },
    ) as ctx:
        transcript = await asr.transcribe(local_uri, p.get("opts"))

        # 字符上限保护（头脑风暴 P0）
        text = transcript.text
        capped = False
        if len(text) > _MAX_TRANSCRIPT_CHARS:
            text = text[:_MAX_TRANSCRIPT_CHARS]
            capped = True

        cv_id = persist_content_version(
            repos,
            ctx.job,
            project_id=ctx.project_id,
            content=text,
            content_type="transcript",
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            producer={
                "kind": "asr",
                "provider": transcript.provider
                or getattr(asr, "name", "unknown"),
                "language": transcript.language,
                "duration_sec": transcript.duration_sec,
                "segments": [s.model_dump() for s in transcript.segments],
            },
            stage=JobStage.TRANSCRIBING,
        )

    return CommandResult(
        ok=True,
        commandId=env.commandId,
        job_id=ctx.job.id,
        artifact_ids=[cv_id],
        detail={
            "asset_id": asset_id,
            "local_uri": local_uri,
            "provider": transcript.provider,
            "language": transcript.language,
            "segment_count": len(transcript.segments),
            "char_count": len(text),
            "capped": capped,
        },
    )
