"""``ImportSource`` 命令处理（W3 Batch 0 + Tranche 2 链接导入）。

PRD-SRC-001「导入本地视频、音频、图片、文本」的后端落点；拖放与文件选择
在 ``features/import/ImportView.tsx``。PRD-SRC-004 的哈希去重见 ``_insert_asset``。

两条导入路径（payload 二选一）：

1. 本地文件 ``{local_uri}``：解析 payload → hash/去重写入 ``source_assets``。
2. 直链 URL ``{url}``（Tranche 2，PRD-SRC-002）：创建 job
   （``JobStage.DOWNLOADING``）→ httpx 流式下载到
   ``$STEPWORK_HOME/assets/<project_id>/`` → 走既有 hash/dedup/ffprobe
   链路。三态错误：``NEED_LOGIN``（401/403）/ ``DOWNLOAD_FAILED``（其它）。

来源记录（PRD-SRC-003）：payload 可选 ``{original_url, author,
rights_declaration}``；``original_url`` 落 ``original_uri`` 列、
``rights_declaration``（original|licensed|reference）落同名列、
``author`` 并入 ``metadata`` JSON。
"""

from __future__ import annotations

import os
from typing import Any

from worker.runtime.cleanup import assets_root, resolve_cleanup_config
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.ingest.download import download_url
from worker.runtime.jobs import content_job, record_result, transition
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
    JobStage,
    JobState,
    SourceAsset,
)

# rights_declaration 合法取值（PRD-SRC-003）
_RIGHTS_VALUES: tuple[str, ...] = ("original", "licensed", "reference")


def _validate_rights(p: dict[str, Any]) -> str | None:
    """校验可选 ``rights_declaration``；非法取值转译为 INVALID_ARGUMENT。"""
    rights = p.get("rights_declaration")
    if rights is None:
        return None
    if rights not in _RIGHTS_VALUES:
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"rights_declaration must be one of {_RIGHTS_VALUES}, got {rights!r}",
        )
    return str(rights)


def _merge_author(metadata: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    """把可选 ``author`` 并入 metadata（source_assets 无独立列）。"""
    author = p.get("author")
    if author:
        metadata = dict(metadata)
        metadata["author"] = str(author)
    return metadata


def _insert_asset(
    repos: Any,
    project_id: str,
    p: dict[str, Any],
    *,
    local_uri: str,
    content_hash: str,
    metadata: dict[str, Any],
    original_uri: str | None,
) -> tuple[str, bool]:
    """构造并去重写入 ``SourceAsset``；返回 ``(asset_id, dedup)``。"""
    asset = SourceAsset(
        project_id=project_id,
        kind=str(p.get("kind", "video")),
        local_uri=local_uri,
        original_uri=original_uri,
        content_hash=content_hash,
        rights_declaration=_validate_rights(p),
        metadata=_merge_author(metadata, p),
    )
    asset_id = repos.source_assets.insert_dedup(asset)
    return asset_id, asset_id != asset.id


async def _handle_url_import(
    env: CommandEnvelope, deps: Deps, project_id: str
) -> CommandResult:
    """URL 直链导入：job(DOWNLOADING) → 下载 → hash/dedup/ffprobe 链路。"""
    repos = deps.repos
    p: dict[str, Any] = env.payload
    url = str(p.get("url"))
    # 先校验 rights（job 创建前，坏参数不残留半截 job）
    _validate_rights(p)

    dest_dir = assets_root() / project_id
    async with content_job(
        repos,
        job_type="import",
        stage=JobStage.DOWNLOADING,
        env=env,
        fail_code="DOWNLOAD_FAILED",
        lease="import_source",
        payload={"url": url, "project_id": project_id},
        notify=deps.notify,
    ) as ctx:
        try:
            result = await download_url(url, dest_dir)
        except DispatchError as e:
            # content_job 对 DispatchError 只透传不转 FAILED——
            # 下载三态（NEED_LOGIN / DOWNLOAD_FAILED）需要 job 落终态
            transition(
                repos, ctx.job.id, JobState.FAILED,
                error=e.code, stage=JobStage.DOWNLOADING,
            )
            raise

        local_path = result.local_path
        content_hash = p.get("content_hash")
        if not content_hash and deps.ingest is not None:
            content_hash = deps.ingest.hash_file(local_path)
        if not content_hash:
            transition(
                repos, ctx.job.id, JobState.FAILED,
                error="DOWNLOAD_FAILED", stage=JobStage.DOWNLOADING,
            )
            raise DispatchError(
                "DOWNLOAD_FAILED",
                "content_hash unavailable and ingest.hash_file not configured",
            )

        # 既有 ffprobe 元数据链路（失败降级为最小安全字段）
        metadata: dict[str, Any] = dict(p.get("metadata", {}))
        if deps.ingest is not None:
            metadata = {**deps.ingest.extract_metadata(local_path), **metadata}
        if result.content_type:
            metadata.setdefault("content_type", result.content_type)

        asset_id, dedup = _insert_asset(
            repos, project_id, p,
            local_uri=local_path,
            content_hash=content_hash,
            metadata=metadata,
            original_uri=p.get("original_url") or p.get("original_uri") or url,
        )
        if dedup:
            # 去重命中：新下载的是重复文件，不留双份
            try:
                os.remove(local_path)
            except OSError:
                pass

        transition(
            repos, ctx.job.id, JobState.SUCCEEDED,
            progress=1.0, stage=JobStage.DOWNLOADING,
        )
        record_result(repos, ctx.job.id, [asset_id])

    return CommandResult(
        ok=True,
        commandId=env.commandId,
        job_id=ctx.job.id,
        artifact_ids=[asset_id],
        detail={
            "asset_id": asset_id,
            "dedup": dedup,
            "local_uri": local_path,
            "url": url,
            "size_bytes": result.size_bytes,
        },
    )


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``ImportSource``（local_uri / url 二选一）。"""
    repos = deps.repos
    p: dict[str, Any] = env.payload

    # 确保目标工作区存在（上游未必先建；按 id 幂等插入）
    ws = repos.workspaces.ensure(env.workspaceId)

    project_id = env.projectId
    if project_id is None:
        project_id = repos.projects.get_or_create_default(env.workspaceId).id

    url = p.get("url")
    local_uri = p.get("local_uri")
    if url and local_uri:
        raise DispatchError(
            "INVALID_ARGUMENT", "provide either url or local_uri, not both"
        )
    if url:
        result = await _handle_url_import(env, deps, project_id)
        # cleanupMode=immediate：导入完成后即清残留中间文件（*.part）
        _, mode = resolve_cleanup_config(ws.settings)
        if mode == "immediate":
            try:
                for part in (assets_root() / project_id).glob("*.part"):
                    part.unlink()
            except OSError:
                pass
        return result

    if not local_uri:
        raise DispatchError("INVALID_ARGUMENT", "local_uri or url is required")

    content_hash = p.get("content_hash")
    if not content_hash and deps.ingest is not None:
        content_hash = deps.ingest.hash_file(local_uri)
    if not content_hash:
        raise DispatchError(
            "INVALID_ARGUMENT",
            "content_hash unavailable and ingest.hash_file not configured",
        )

    asset_id, dedup = _insert_asset(
        repos, project_id, p,
        local_uri=local_uri,
        content_hash=content_hash,
        metadata=p.get("metadata", {}),
        original_uri=p.get("original_url") or p.get("original_uri"),
    )

    return CommandResult(
        ok=True,
        commandId=env.commandId,
        artifact_ids=[asset_id],
        detail={"asset_id": asset_id, "dedup": dedup},
    )
