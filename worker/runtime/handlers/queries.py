"""只读查询类命令处理（W7 Phase 3 + Tranche 1 ListJobs + Tranche 2 版本查询）。

六个查询 handler，全部只读、不写库：

- ``ListProjects``：列出某工作区下的内容项目。
- ``GetProject``：按 id 取单个项目（兼容 ``payload.projectId`` /
  ``payload.project_id`` / 信封顶层 ``projectId`` 三种来源）。
- ``GetJobStatus``：按 id 取任务状态。
- ``ListJobs``：按 ``created_at DESC`` 列出任务，支持 ``states``（小写
  ``JobState`` value 列表）过滤与 ``limit``（默认 50）截断。
- ``ListContentVersions``（Tranche 2，T10 版本恢复地基）：按项目列出内容
  版本（``created_at DESC``），支持 ``contentType`` 过滤与 ``limit``
  （默认 20），条目含 ``preview``（内容前 200 字符）。
- ``GetContentVersion``（Tranche 2）：按 id 取单版本完整内容。

``Repos`` 暂未暴露 list / get-by-id 等读方法，故在 ``deps.repos.conn`` 上
做只读 ``SELECT``；读取严格只读，绝不修改任何状态。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.db.repos import _row_to_job, _row_to_source_asset
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult, Job
from worker.runtime.render.templates import ASPECT_PRESETS, list_templates
from worker.runtime.script.diff import (
    diff_lines,
    extract_text,
    find_ai_draft,
    summarize,
)

_DEFAULT_LIST_JOBS_LIMIT = 50
"""``ListJobs`` 缺省返回条数上限。"""

_DEFAULT_LIST_VERSIONS_LIMIT = 20
"""``ListContentVersions`` 缺省返回条数上限。"""

_DEFAULT_LIST_ASSETS_LIMIT = 100
"""``ListSourceAssets`` 缺省返回条数上限（PRD-SRC-003）。"""

_PREVIEW_CHARS = 200
"""``ListContentVersions`` 条目 ``preview`` 的字符上限。"""


def _load_producer(raw: Any) -> dict[str, Any]:
    """把 ``producer`` JSON 列解析为 dict（畸形时降级为空对象）。"""
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _asset_to_dict(asset: Any) -> dict[str, Any]:
    """SourceAsset → 可追溯字段（PRD-SRC-003：来源/作者/导入时间/权利声明）。

    author 落在 metadata 里（见 import_source._merge_author），这里提到顶层
    方便前端与 CLI 直接展示，不必各自去 metadata 里翻。
    """
    metadata = asset.metadata or {}
    return {
        "id": asset.id,
        "project_id": asset.project_id,
        "kind": asset.kind,
        "local_uri": asset.local_uri,
        "original_uri": asset.original_uri,
        "content_hash": asset.content_hash,
        "rights_declaration": asset.rights_declaration,
        "author": metadata.get("author"),
        "created_at": asset.created_at,
        "metadata": metadata,
    }


#: LIKE 转义符（配合 SQL 的 ``ESCAPE`` 子句）
_LIKE_ESCAPE = "\\"


def _escape_like(value: str) -> str:
    """转义 SQL LIKE 的通配符（``%`` / ``_``）与转义符本身。

    调用方必须配 ``ESCAPE '\\'``。不转义的话用户搜 ``A_B`` 会匹到 ``AXB``、
    搜 ``100%`` 会匹配一切。
    """
    escaped = value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
    escaped = escaped.replace("%", f"{_LIKE_ESCAPE}%")
    return escaped.replace("_", f"{_LIKE_ESCAPE}_")


def _load_tags(row: Any) -> list[str]:
    """解析 ``tags`` JSON 列；缺列/畸形一律返回空列表。"""
    if "tags" not in row.keys():
        return []
    try:
        parsed = json.loads(row["tags"]) if row["tags"] else []
    except (TypeError, ValueError):
        return []
    return [str(t) for t in parsed] if isinstance(parsed, list) else []


def _project_row_to_dict(row: Any) -> dict[str, Any]:
    """把 ``content_projects`` 行转为可序列化的 dict（列名 → 值）。"""
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "title": str(row["title"]),
        "status": str(row["status"]),
        "brand_profile_id": (
            str(row["brand_profile_id"])
            if row["brand_profile_id"] is not None
            else None
        ),
        "current_content_version_id": (
            str(row["current_content_version_id"])
            if row["current_content_version_id"] is not None
            else None
        ),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        # PRD-WS-003：标签与最近访问（旧库未跑 0007 时兜底）
        "tags": _load_tags(row),
        "last_accessed_at": (
            row["last_accessed_at"] if "last_accessed_at" in row.keys() else None
        ),
    }


def _resolve_project_id(env: CommandEnvelope) -> str | None:
    """从 payload 或信封顶层解析 projectId（兼容两种命名）。"""
    payload = env.payload or {}
    return payload.get("projectId") or payload.get("project_id") or env.projectId


def _job_to_dict(job: Job) -> dict[str, Any]:
    """把 :class:`Job` 转为可序列化 dict（``GetJobStatus`` / ``ListJobs`` 同构）。"""
    return {
        "id": job.id,
        "job_type": job.job_type,
        "state": job.state.value,
        "stage": job.stage.value if job.stage else None,
        "progress": job.progress,
        "attempt_count": job.attempt_count,
        "error_code": job.error_code,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由 ``ListProjects`` / ``GetProject`` / ``GetJobStatus`` 三个查询命令。"""
    if env.commandType == "ListProjects":
        # PRD-WS-003「项目搜索、标签与最近访问」：此前无任何查询参数，
        # 前端只能对已拉取列表做本地 title 过滤（数据多了就不准）。
        payload = env.payload or {}
        sql = "SELECT * FROM content_projects WHERE workspace_id=?"
        args: list[Any] = [env.workspaceId]

        keyword = payload.get("keyword")
        if keyword:
            if not isinstance(keyword, str):
                raise DispatchError("INVALID_ARGUMENT", "keyword must be a string")
            # 转义 LIKE 通配符：否则用户搜 "A_B" 会把 "AXB" 也搜出来，
            # 搜 "100%" 更是匹配一切
            sql += f" AND title LIKE ? ESCAPE '{_LIKE_ESCAPE}'"
            args.append(f"%{_escape_like(keyword)}%")

        status_filter = payload.get("status")
        if status_filter:
            if not isinstance(status_filter, str):
                raise DispatchError("INVALID_ARGUMENT", "status must be a string")
            sql += " AND status=?"
            args.append(status_filter)

        tags = payload.get("tags")
        if tags:
            if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
                raise DispatchError(
                    "INVALID_ARGUMENT", "tags must be a list of strings"
                )
            # tags 存 JSON 数组文本，用 LIKE 做包含匹配（本地单用户量级足够）；
            # 多标签为 AND 语义（同时含有）。
            for tag in tags:
                sql += f" AND tags LIKE ? ESCAPE '{_LIKE_ESCAPE}'"
                # 标签值在库里是 json.dumps 后的形态，故按同样规则转义后
                # 再套通配符，避免含引号/反斜杠的标签永远匹配不到
                encoded = json.dumps(str(tag), ensure_ascii=False)[1:-1]
                args.append(f'%"{_escape_like(encoded)}"%')

        # PRD-WS-003「最近访问」：默认按最近访问排序，回落创建时间
        # （首页「最近项目」此前实为 created_at 倒序，访问过也不会靠前）
        sort = str(payload.get("sort") or "recent")
        if sort == "recent":
            sql += " ORDER BY COALESCE(last_accessed_at, created_at) DESC"
        elif sort == "created":
            sql += " ORDER BY created_at DESC"
        elif sort == "title":
            sql += " ORDER BY title ASC"
        else:
            raise DispatchError(
                "INVALID_ARGUMENT", f"unknown sort {sort!r}"
            )

        rows = deps.repos.conn.execute(sql, tuple(args)).fetchall()
        projects = [_project_row_to_dict(r) for r in rows]
        # 供 UI 渲染标签筛选器：取**全工作区**的标签，而不是筛选后的结果 ——
        # 否则按某标签筛选后其它标签就消失了，用户无法切换筛选条件。
        all_rows = deps.repos.conn.execute(
            "SELECT tags FROM content_projects WHERE workspace_id=?",
            (env.workspaceId,),
        ).fetchall()
        all_tags: set[str] = set()
        for tag_row in all_rows:
            all_tags.update(_load_tags(tag_row))
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"projects": projects, "available_tags": sorted(all_tags)},
        )

    if env.commandType == "GetProject":
        pid = _resolve_project_id(env)
        if not pid:
            raise DispatchError("INVALID_ARGUMENT", "missing projectId")
        row = deps.repos.conn.execute(
            "SELECT * FROM content_projects WHERE id=?", (pid,)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"project {pid!r} not found")
        # PRD-WS-003「最近访问」：打开项目即刷新访问时间。与 updated_at
        # （内容变更时间）分开——后者建完项目就不再变，无法表达「最近用过」。
        deps.repos.conn.execute(
            "UPDATE content_projects SET last_accessed_at=? WHERE id=?",
            (datetime.now(UTC).isoformat(), pid),
        )
        deps.repos.conn.commit()
        row = deps.repos.conn.execute(
            "SELECT * FROM content_projects WHERE id=?", (pid,)
        ).fetchone()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"project": _project_row_to_dict(row)},
        )

    if env.commandType == "GetJobStatus":
        payload = env.payload or {}
        job_id = payload.get("jobId") or payload.get("job_id")
        if not job_id:
            raise DispatchError("INVALID_ARGUMENT", "missing jobId")
        job = deps.repos.jobs.get(job_id)
        if job is None:
            raise DispatchError("NOT_FOUND", f"job {job_id!r} not found")
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"job": _job_to_dict(job)},
        )

    if env.commandType == "ListJobs":
        payload = env.payload or {}
        states = payload.get("states")
        limit = payload.get("limit", _DEFAULT_LIST_JOBS_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise DispatchError(
                "INVALID_ARGUMENT", f"limit must be a positive integer, got {limit!r}"
            )
        sql = "SELECT * FROM jobs"
        args = []  # 复用上文 list[Any]（同作用域重复注解会 no-redef）
        if states is not None:
            if not isinstance(states, list) or not all(
                isinstance(s, str) for s in states
            ):
                raise DispatchError(
                    "INVALID_ARGUMENT", "states must be a list of strings"
                )
            if not states:
                # 显式空列表 → 空结果（与「未提供 = 不过滤」区分开）
                return CommandResult(
                    ok=True, commandId=env.commandId, detail={"jobs": []}
                )
            placeholders = ",".join(["?"] * len(states))
            sql += f" WHERE state IN ({placeholders})"
            args.extend(states)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        rows = deps.repos.conn.execute(sql, tuple(args)).fetchall()
        # 复用 repos 的行→模型映射，保证 state/stage 枚举与 GetJobStatus 同构
        jobs = [_job_to_dict(_row_to_job(r)) for r in rows]
        return CommandResult(
            ok=True, commandId=env.commandId, detail={"jobs": jobs}
        )

    if env.commandType == "ListContentVersions":
        payload = env.payload or {}
        pid = _resolve_project_id(env)
        if not pid:
            raise DispatchError("INVALID_ARGUMENT", "missing projectId")
        limit = payload.get("limit", _DEFAULT_LIST_VERSIONS_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise DispatchError(
                "INVALID_ARGUMENT", f"limit must be a positive integer, got {limit!r}"
            )
        content_type = payload.get("contentType") or payload.get("content_type")
        sql = (
            "SELECT id, content_type, parent_version_id, created_at, "
            "producer, content FROM content_versions WHERE project_id=?"
        )
        args = [pid]  # 复用上文 list[Any]（避免同作用域重复注解 no-redef）
        if content_type is not None:
            if not isinstance(content_type, str):
                raise DispatchError(
                    "INVALID_ARGUMENT", "contentType must be a string"
                )
            sql += " AND content_type=?"
            args.append(content_type)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        rows = deps.repos.conn.execute(sql, tuple(args)).fetchall()
        versions = [
            {
                "id": str(r["id"]),
                "content_type": str(r["content_type"]),
                "parent_version_id": (
                    str(r["parent_version_id"])
                    if r["parent_version_id"] is not None
                    else None
                ),
                "created_at": str(r["created_at"]),
                "producer": _load_producer(r["producer"]),
                "preview": str(r["content"])[:_PREVIEW_CHARS],
            }
            for r in rows
        ]
        return CommandResult(
            ok=True, commandId=env.commandId, detail={"versions": versions}
        )

    if env.commandType == "GetContentVersion":
        payload = env.payload or {}
        version_id = payload.get("versionId") or payload.get("version_id")
        if not version_id:
            raise DispatchError("INVALID_ARGUMENT", "missing versionId")
        cv = deps.repos.content_versions.get(version_id)
        if cv is None:
            raise DispatchError("NOT_FOUND", f"version {version_id!r} not found")
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "version": {
                    "id": cv.id,
                    "project_id": cv.project_id,
                    "content_type": cv.content_type,
                    "content": cv.content,
                    "parent_version_id": cv.parent_version_id,
                    "created_at": cv.created_at,
                    "producer": cv.producer,
                }
            },
        )

    if env.commandType in ("ListSourceAssets", "GetSourceAsset"):
        # PRD-SRC-003：素材此前只有写入（ImportSource）与删除（DeleteAsset），
        # 没有任何读命令 —— 「每个 SourceAsset 均可追溯」无从谈起（前端素材
        # 列表也只存在于内存 store，刷新即丢）。这里补上读取路径，返回来源、
        # 作者、导入时间与权利声明。
        if env.commandType == "GetSourceAsset":
            payload = env.payload or {}
            asset_id = payload.get("assetId") or payload.get("asset_id")
            if not asset_id:
                raise DispatchError("INVALID_ARGUMENT", "assetId required")
            asset = deps.repos.source_assets.get(str(asset_id))
            if asset is None:
                raise DispatchError("NOT_FOUND", f"asset {asset_id!r} not found")
            return CommandResult(
                ok=True,
                commandId=env.commandId,
                detail={"asset": _asset_to_dict(asset)},
            )

        pid = _resolve_project_id(env)
        if not pid:
            raise DispatchError("INVALID_ARGUMENT", "missing projectId")
        limit = (env.payload or {}).get("limit", _DEFAULT_LIST_ASSETS_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise DispatchError(
                "INVALID_ARGUMENT", f"limit must be a positive integer, got {limit!r}"
            )
        rows = deps.repos.conn.execute(
            "SELECT * FROM source_assets WHERE project_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (pid, limit),
        ).fetchall()
        assets = [_asset_to_dict(_row_to_source_asset(r)) for r in rows]
        return CommandResult(
            ok=True, commandId=env.commandId, detail={"assets": assets}
        )

    if env.commandType == "DiffContentVersions":
        # PRD-SCR-006「可比较 AI 初稿和最终稿」：版本链此前已完备，但没有
        # 比较能力——用户只能各自打开两个版本肉眼比对，也没有「AI 初稿」
        # 这个锚点。baseVersionId 省略时自动沿 parent 链定位 AI 初稿。
        payload = env.payload or {}
        target_id = payload.get("versionId") or payload.get("version_id")
        if not target_id:
            raise DispatchError("INVALID_ARGUMENT", "versionId required")
        target = deps.repos.content_versions.get(str(target_id))
        if target is None:
            raise DispatchError("NOT_FOUND", f"version {target_id!r} not found")

        base_id = payload.get("baseVersionId") or payload.get("base_version_id")
        if not base_id:
            base_id = find_ai_draft(deps.repos.conn, str(target_id))
            if not base_id:
                base_id = target.parent_version_id
        if not base_id:
            raise DispatchError(
                "INVALID_ARGUMENT",
                "no base version to compare (no AI draft and no parent)",
            )
        base = deps.repos.content_versions.get(str(base_id))
        if base is None:
            raise DispatchError("NOT_FOUND", f"version {base_id!r} not found")
        if base.project_id != target.project_id:
            raise DispatchError(
                "INVALID_ARGUMENT", "versions belong to different projects"
            )

        before_text, before_title = extract_text(base.content or "")
        after_text, after_title = extract_text(target.content or "")
        lines = diff_lines(before_text, after_text)
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "base_version_id": str(base_id),
                "target_version_id": str(target_id),
                "base_is_ai_draft": (base.producer or {}).get("kind") == "ai-script",
                "base_title": before_title,
                "target_title": after_title,
                "lines": lines,
                "summary": summarize(lines),
            },
        )

    if env.commandType == "ListRenderTemplates":
        # PRD-REN-005：模板与画幅是后端注册表的事实，前端/CLI 不再硬编码，
        # 避免出现「UI 有选项、后端不认」的错配。
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "templates": list_templates(),
                "aspects": [
                    {"id": name, "resolution": list(size)}
                    for name, size in ASPECT_PRESETS.items()
                ],
            },
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by queries handler",
    )
