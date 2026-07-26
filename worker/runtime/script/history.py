"""历史内容检索（供 PRD-SCR-004 / PRD-SCR-005 的相似度提醒）。

PRD-SCR-004 要求「根据**项目和账号历史**提示相似主题」，因此检索范围不能
只看当前项目：同一 BrandProfile 关联的其它项目也要纳入（同一账号下的
选题重复才是用户真正在意的）。
"""

from __future__ import annotations

import json
from typing import Any

#: 单次比较的历史条数上限（防止老项目里几千条版本拖慢生成）
_MAX_HISTORY = 200


def _related_project_ids(conn: Any, project_id: str) -> list[str]:
    """当前项目 + 同一 BrandProfile 下的其它项目（账号级历史）。"""
    row = conn.execute(
        "SELECT workspace_id, brand_profile_id FROM content_projects WHERE id=?",
        (project_id,),
    ).fetchone()
    if row is None:
        return [project_id]
    brand_id = row["brand_profile_id"]
    if not brand_id:
        return [project_id]
    rows = conn.execute(
        "SELECT id FROM content_projects WHERE brand_profile_id=?", (brand_id,)
    ).fetchall()
    ids = [str(r["id"]) for r in rows]
    return ids or [project_id]


def load_topic_history(
    conn: Any, project_id: str, *, exclude_version_id: str | None = None
) -> list[dict[str, Any]]:
    """取历史选题角度（跨同账号项目），每个角度一条候选。"""
    project_ids = _related_project_ids(conn, project_id)
    placeholders = ",".join(["?"] * len(project_ids))
    rows = conn.execute(
        f"SELECT id, content FROM content_versions "  # noqa: S608 - 占位符按数量生成
        f"WHERE project_id IN ({placeholders}) AND content_type='topic_proposal' "
        f"ORDER BY created_at DESC LIMIT ?",
        (*project_ids, _MAX_HISTORY),
    ).fetchall()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        if exclude_version_id and str(row["id"]) == exclude_version_id:
            continue
        try:
            parsed = json.loads(row["content"])
        except (TypeError, ValueError):
            continue
        for angle in (parsed or {}).get("angles", []):
            title = str(angle.get("title") or "")
            if not title:
                continue
            candidates.append(
                {
                    "id": f"{row['id']}:{angle.get('id') or ''}",
                    # 标题 + 差异化依据一起比，避免只看标题误判
                    "text": f"{title} {angle.get('rationale') or ''}",
                    "label": title,
                }
            )
    return candidates


def load_script_history(
    conn: Any, project_id: str, *, exclude_version_id: str | None = None
) -> list[dict[str, Any]]:
    """取历史脚本正文（跨同账号项目），供原创性提醒比对。"""
    project_ids = _related_project_ids(conn, project_id)
    placeholders = ",".join(["?"] * len(project_ids))
    rows = conn.execute(
        f"SELECT id, content, created_at FROM content_versions "  # noqa: S608
        f"WHERE project_id IN ({placeholders}) AND content_type='script' "
        f"ORDER BY created_at DESC LIMIT ?",
        (*project_ids, _MAX_HISTORY),
    ).fetchall()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        if exclude_version_id and str(row["id"]) == exclude_version_id:
            continue
        body, title = _extract_script_text(str(row["content"] or ""))
        if not body.strip():
            continue
        candidates.append(
            {
                "id": str(row["id"]),
                "text": body,
                "label": title or str(row["id"])[:8],
            }
        )
    return candidates


def _extract_script_text(content: str) -> tuple[str, str]:
    """脚本内容 → ``(正文, 标题)``。

    兼容三种落库形态：``{"title","body"}``（GenerateScript）、
    ``{"text","title"}``（编辑器保存）、裸文本。
    """
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return content, ""
    if not isinstance(parsed, dict):
        return content, ""
    for key in ("body", "text"):
        value = parsed.get(key)
        if isinstance(value, str):
            return value, str(parsed.get("title") or "")
    return content, str(parsed.get("title") or "")
