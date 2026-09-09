"""``video_scenes`` 分幕命令处理（S2 地基）。

``migrations/0012`` 建了分幕表，但**表本身不产生任何价值**——本仓已经有
一堆「只有 .gitkeep 的空壳目录」了，别再多一张没人读写的空表。本模块让它
真正接进命令总线，GUI / CLI / Agent 三条外壳都能摸到它（P4）。

三个命令：

- ``SaveVideoScenes``：payload {versionId, scenes: [{seq, text, emotion?,
  highlight?}], replace?=true} → 落库。文案阶段产出分幕后调用。
- ``ListVideoScenes``：payload {versionId} → 按 seq 升序列出。
- ``UpdateVideoScene``：payload {sceneId, audioUri?, imageUri?, durationSec?,
  bornAtSec?} → 回填配音/配图的产出。单幕可重渲就靠它。

**固化的隐性规则**（摘自已验证流水线，过去是静默失效）：

- ``highlight`` 必须是 ``text`` 的子串 —— 否则标红不生效，且没有任何报错，
  成片看起来就是「忘了加高亮」。这里直接在入口拦掉。
- ``duration_sec`` 由 TTS **实测**回填，不接受估值：画面被音频时长驱动，
  反过来就是 huashu-design 说的「失败模式 #1 = 带配音的 PPT」。
"""

from __future__ import annotations

from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult, VideoScene

_ALLOWED_UPDATE_FIELDS: dict[str, str] = {
    "audioUri": "audio_uri",
    "imageUri": "image_uri",
    "durationSec": "duration_sec",
    "bornAtSec": "born_at_sec",
}


def _scene_from_payload(version_id: str, raw: Any, idx: int) -> VideoScene:
    """把 payload 里的一幕转成 :class:`VideoScene`；不合法即抛。"""
    if not isinstance(raw, dict):
        raise DispatchError("INVALID_ARGUMENT", f"scenes[{idx}] must be an object")
    if "seq" not in raw:
        raise DispatchError("INVALID_ARGUMENT", f"scenes[{idx}].seq required")
    try:
        seq = int(raw["seq"])
    except (TypeError, ValueError):
        raise DispatchError(
            "INVALID_ARGUMENT", f"scenes[{idx}].seq must be an integer"
        ) from None
    text = raw.get("text")
    if text is not None and not isinstance(text, str):
        raise DispatchError(
            "INVALID_ARGUMENT", f"scenes[{idx}].text must be a string"
        )
    highlight = raw.get("highlight")
    if highlight is not None:
        if not isinstance(highlight, str):
            raise DispatchError(
                "INVALID_ARGUMENT", f"scenes[{idx}].highlight must be a string"
            )
        # 隐性规则固化：非子串 → 标红静默失效，宁可入口报错
        if highlight and highlight not in (text or ""):
            raise DispatchError(
                "INVALID_ARGUMENT",
                f"scenes[{idx}].highlight must be a substring of text "
                f"(got {highlight!r})",
            )
    return VideoScene(
        version_id=version_id,
        seq=seq,
        text=text or "",
        emotion=raw.get("emotion"),
        highlight=highlight,
        start_sec=float(raw.get("startSec") or 0.0),
        # duration 允许为 0：TTS 还没跑。但一旦给了必须是数值
        duration_sec=float(raw.get("durationSec") or 0.0),
        born_at_sec=raw.get("bornAtSec"),
    )


def _resolve_project(repos: Any, env: CommandEnvelope) -> str:
    ws = repos.workspaces.ensure(env.workspaceId)
    del ws  # ensure 只保证存在；project 另取
    return str(
        env.projectId or repos.projects.get_or_create_default(env.workspaceId).id
    )


async def _handle_save(env: CommandEnvelope, deps: Deps) -> CommandResult:
    payload = dict(env.payload)
    version_id = payload.get("versionId") or payload.get("version_id")
    if not version_id:
        raise DispatchError("INVALID_ARGUMENT", "versionId required")
    raw_scenes = payload.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        # 空列表 == 「删光所有幕」：语义太危险，宁可要求显式调用方想清楚
        raise DispatchError("INVALID_ARGUMENT", "scenes must be a non-empty array")

    repos = deps.repos
    project_id = _resolve_project(repos, env)
    src = repos.content_versions.get(str(version_id))
    if src is None or src.project_id != project_id:
        raise DispatchError("NOT_FOUND", f"version {version_id} not found")

    scenes = [_scene_from_payload(str(version_id), s, i) for i, s in enumerate(raw_scenes)]
    # 同批 seq 重复也要早拦：否则撞 UNIQUE 只会得到一个裸 IntegrityError
    seqs = [s.seq for s in scenes]
    if len(set(seqs)) != len(seqs):
        raise DispatchError("INVALID_ARGUMENT", "duplicate seq in scenes")

    replace = payload.get("replace", True)
    if replace:
        repos.video_scenes.replace_for_version(str(version_id), scenes)
    else:
        repos.video_scenes.insert_many(scenes)

    return CommandResult(
        ok=True,
        commandId=env.commandId,
        detail={
            "versionId": str(version_id),
            "count": len(scenes),
            "sceneIds": [s.id for s in scenes],
            "replaced": bool(replace),
        },
    )


async def _handle_list(env: CommandEnvelope, deps: Deps) -> CommandResult:
    payload = dict(env.payload)
    version_id = payload.get("versionId") or payload.get("version_id")
    if not version_id:
        raise DispatchError("INVALID_ARGUMENT", "versionId required")
    repos = deps.repos
    project_id = _resolve_project(repos, env)
    src = repos.content_versions.get(str(version_id))
    if src is None or src.project_id != project_id:
        raise DispatchError("NOT_FOUND", f"version {version_id} not found")
    scenes = repos.video_scenes.list_by_version(str(version_id))
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        detail={
            "versionId": str(version_id),
            "count": len(scenes),
            "scenes": [s.model_dump() for s in scenes],
        },
    )


async def _handle_update(env: CommandEnvelope, deps: Deps) -> CommandResult:
    payload = dict(env.payload)
    scene_id = payload.get("sceneId") or payload.get("scene_id")
    if not scene_id:
        raise DispatchError("INVALID_ARGUMENT", "sceneId required")
    fields: dict[str, Any] = {}
    for camel, snake in _ALLOWED_UPDATE_FIELDS.items():
        if camel in payload and payload[camel] is not None:
            fields[snake] = payload[camel]
    if snake_keys := (set(payload) - set(_ALLOWED_UPDATE_FIELDS) - {"sceneId", "scene_id"}):
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"unsupported fields for UpdateVideoScene: {sorted(snake_keys)}",
        )
    if not fields:
        raise DispatchError("INVALID_ARGUMENT", "nothing to update")

    repos = deps.repos
    existing = repos.video_scenes.get(str(scene_id))
    if existing is None:
        raise DispatchError("NOT_FOUND", f"scene {scene_id} not found")
    updated = repos.video_scenes.update_production(str(scene_id), **fields)
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        detail={"scene": updated.model_dump() if updated else None},
    )


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """按 ``commandType`` 分派分幕命令。"""
    if env.commandType == "SaveVideoScenes":
        return await _handle_save(env, deps)
    if env.commandType == "ListVideoScenes":
        return await _handle_list(env, deps)
    if env.commandType == "UpdateVideoScene":
        return await _handle_update(env, deps)
    raise DispatchError("INVALID_ARGUMENT", f"unsupported command {env.commandType}")
