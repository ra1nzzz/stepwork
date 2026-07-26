"""BrandProfile 命令处理（Tranche 2，PRD-BRD-001/002 + SCR-002）。

四个命令：

- ``CreateBrandProfile``：payload {name, positioning?, audience?, tone?,
  contentPillars?: string[], bannedExpressions?: string[]} → detail.profile
  （全字段 camelCase 出参）。
- ``UpdateBrandProfile``：payload {profileId, ...同上可选字段} →
  detail.profile；刷新 ``updated_at``。
- ``ListBrandProfiles``：按 ``created_at DESC`` 列出本工作区的画像。
- ``SetProjectBrandProfile``：payload {projectId, profileId|null} →
  写 ``content_projects.brand_profile_id``（null = 解绑）。

生成注入辅助（供 generate_topic / generate_script 复用）：

- :func:`load_project_brand`：按 project 解析已关联画像行（无则 None）。
- :func:`format_brand_prompt_block`：把画像格式化为提示词注入块
  （语气/定位/受众/内容支柱/禁用表达；禁用表达明确指示「不得使用以下表达」）。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult

# 可选文本字段（payload camelCase 名 → 列名同名小写蛇形）
_TEXT_FIELDS: tuple[str, ...] = ("positioning", "audience", "tone")
# 可选 JSON 数组字段（payload camelCase 名 → 列名）
_LIST_FIELDS: dict[str, str] = {
    "contentPillars": "content_pillars",
    "bannedExpressions": "banned_expressions",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_list(raw: Any) -> list[str]:
    """把 JSON 列 TEXT 解析为字符串列表（畸形时降级为空列表）。"""
    try:
        parsed = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _validate_list(payload: dict[str, Any], key: str) -> list[str] | None:
    """校验 payload 中的可选数组字段；未提供返回 None，畸形抛错。"""
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise DispatchError("INVALID_ARGUMENT", f"{key} must be a list of strings")
    return value


def _row_to_profile(row: Any) -> dict[str, Any]:
    """把 ``brand_profiles`` 行转为 camelCase 出参 dict。"""
    return {
        "id": str(row["id"]),
        "workspaceId": str(row["workspace_id"]),
        "name": str(row["name"]),
        "positioning": str(row["positioning"] or ""),
        "audience": str(row["audience"] or ""),
        "tone": str(row["tone"] or ""),
        "contentPillars": _load_list(row["content_pillars"]),
        "bannedExpressions": _load_list(row["banned_expressions"]),
        "createdAt": str(row["created_at"] or ""),
        "updatedAt": str(row["updated_at"] or ""),
    }


def _get_profile_row(conn: Any, profile_id: str) -> Any:
    return conn.execute(
        "SELECT * FROM brand_profiles WHERE id=?", (profile_id,)
    ).fetchone()


def load_project_brand(repos: Any, project_id: str) -> dict[str, Any] | None:
    """按项目解析已关联的品牌画像（camelCase dict；未关联/不存在返回 None）。"""
    row = repos.conn.execute(
        "SELECT brand_profile_id FROM content_projects WHERE id=?", (project_id,)
    ).fetchone()
    if row is None or row["brand_profile_id"] is None:
        return None
    profile_row = _get_profile_row(repos.conn, str(row["brand_profile_id"]))
    if profile_row is None:
        return None
    return _row_to_profile(profile_row)


def format_brand_prompt_block(profile: dict[str, Any]) -> str:
    """把画像格式化为提示词注入块（禁用表达明确指示不得使用）。"""
    lines: list[str] = [f"品牌画像「{profile.get('name', '')}」约束："]
    if profile.get("tone"):
        lines.append(f"- 语气：{profile['tone']}")
    if profile.get("positioning"):
        lines.append(f"- 定位：{profile['positioning']}")
    if profile.get("audience"):
        lines.append(f"- 受众：{profile['audience']}")
    pillars = profile.get("contentPillars") or []
    if pillars:
        lines.append(f"- 内容支柱：{'、'.join(pillars)}")
    banned = profile.get("bannedExpressions") or []
    if banned:
        lines.append(f"- 不得使用以下表达：{'、'.join(banned)}")
    return "\n".join(lines)


def brand_producer_fields(profile: dict[str, Any] | None) -> dict[str, Any]:
    """producer 记录字段（无画像时为 null，保持向后兼容）。"""
    return {
        "brand_profile_id": profile.get("id") if profile else None,
        "brand_profile_updated_at": profile.get("updatedAt") if profile else None,
    }


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由 BrandProfile 四命令。"""
    repos = deps.repos
    p: dict[str, Any] = env.payload or {}

    if env.commandType == "CreateBrandProfile":
        name = p.get("name")
        if not name or not isinstance(name, str):
            raise DispatchError("INVALID_ARGUMENT", "name required")
        repos.workspaces.ensure(env.workspaceId)
        # 独立命名，避免与后续分支的 p.get("profileId")（Any|None）复用同名
        new_profile_id = f"bp_{uuid.uuid4().hex}"
        now = _now()
        pillars = _validate_list(p, "contentPillars") or []
        banned = _validate_list(p, "bannedExpressions") or []
        repos.conn.execute(
            "INSERT INTO brand_profiles "
            "(id, workspace_id, name, positioning, audience, tone, "
            "content_pillars, banned_expressions, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_profile_id, env.workspaceId, name,
                str(p.get("positioning") or ""),
                str(p.get("audience") or ""),
                str(p.get("tone") or ""),
                json.dumps(pillars, ensure_ascii=False),
                json.dumps(banned, ensure_ascii=False),
                now, now,
            ),
        )
        repos.conn.commit()
        row = _get_profile_row(repos.conn, new_profile_id)
        return CommandResult(
            ok=True, commandId=env.commandId,
            detail={"profile": _row_to_profile(row)},
        )

    if env.commandType == "UpdateBrandProfile":
        profile_id = p.get("profileId")
        if not profile_id:
            raise DispatchError("INVALID_ARGUMENT", "profileId required")
        row = _get_profile_row(repos.conn, profile_id)
        if row is None:
            raise DispatchError("NOT_FOUND", f"brand profile {profile_id!r} not found")

        sets: list[str] = []
        args: list[Any] = []
        if "name" in p and p["name"] is not None:
            if not isinstance(p["name"], str) or not p["name"]:
                raise DispatchError("INVALID_ARGUMENT", "name must be a non-empty string")
            sets.append("name=?")
            args.append(p["name"])
        for field in _TEXT_FIELDS:
            if field in p and p[field] is not None:
                sets.append(f"{field}=?")
                args.append(str(p[field]))
        for payload_key, column in _LIST_FIELDS.items():
            value = _validate_list(p, payload_key)
            if value is not None:
                sets.append(f"{column}=?")
                args.append(json.dumps(value, ensure_ascii=False))
        sets.append("updated_at=?")
        args.append(_now())
        args.append(profile_id)
        repos.conn.execute(
            f"UPDATE brand_profiles SET {', '.join(sets)} WHERE id=?", tuple(args)
        )
        repos.conn.commit()
        row = _get_profile_row(repos.conn, profile_id)
        return CommandResult(
            ok=True, commandId=env.commandId,
            detail={"profile": _row_to_profile(row)},
        )

    if env.commandType == "ListBrandProfiles":
        rows = repos.conn.execute(
            "SELECT * FROM brand_profiles WHERE workspace_id=? "
            "ORDER BY created_at DESC",
            (env.workspaceId,),
        ).fetchall()
        return CommandResult(
            ok=True, commandId=env.commandId,
            detail={"profiles": [_row_to_profile(r) for r in rows]},
        )

    if env.commandType == "SetProjectBrandProfile":
        project_id = p.get("projectId")
        if not project_id:
            raise DispatchError("INVALID_ARGUMENT", "projectId required")
        prj = repos.conn.execute(
            "SELECT id FROM content_projects WHERE id=?", (project_id,)
        ).fetchone()
        if prj is None:
            raise DispatchError("NOT_FOUND", f"project {project_id!r} not found")
        profile_id = p.get("profileId")
        if profile_id is not None:
            if _get_profile_row(repos.conn, profile_id) is None:
                raise DispatchError(
                    "NOT_FOUND", f"brand profile {profile_id!r} not found"
                )
        repos.conn.execute(
            "UPDATE content_projects SET brand_profile_id=?, updated_at=? WHERE id=?",
            (profile_id, _now(), project_id),
        )
        repos.conn.commit()
        return CommandResult(
            ok=True, commandId=env.commandId,
            detail={"project_id": project_id, "brand_profile_id": profile_id},
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by brand handler",
    )
