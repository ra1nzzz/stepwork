"""项目导出/导入 handler（W9 L.39）。

路由两个命令：

- ``ExportProject``：把指定项目及其 content_versions / source_assets /
  （可选）jobs 打成 zip bundle，输出到 ``$STEPWORK_HOME/exports/``。
- ``ImportProject``：从 zip bundle 恢复项目到当前工作区，按依赖顺序插入
  （project → assets → versions → jobs）；``remapId=True`` 时所有 id 重映射
  为新 uuid，避免与既有数据冲突。

安全模型（P0 R8）：

- 导入时用 ``zipfile.ZipFile.infolist()`` 校验每个成员名不含 ``..``，
  防止路径穿越攻击。
- ``bundlePath`` 必须存在且为 ``.zip`` 文件。

zip 内容（每个一个 JSON 文件）：

- ``manifest.json``：schema_version / exported_at / project_id / 各表计数
- ``project.json``：project 行（dict，列名 → 值）
- ``versions.json``：list[dict]
- ``assets.json``：list[dict]
- ``jobs.json``：list[dict]（仅 includeJobs=True 时包含）
"""

from __future__ import annotations

import json
import os
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult

# 导出 bundle 输出目录名（$STEPWORK_HOME/exports/）
_EXPORTS_DIR: str = "exports"

# bundle schema 版本（manifest.json 中声明）
_SCHEMA_VERSION: str = "1"


def _resolve_stepwork_home() -> Path:
    """解析 ``$STEPWORK_HOME``，缺省回退到 ``~/STEPWORK``（与 bootstrap.py 一致）。"""
    home = os.environ.get("STEPWORK_HOME") or str(Path.home() / "STEPWORK")
    return Path(home)


def _resolve_project_id(env: CommandEnvelope) -> str | None:
    """从 payload 或信封顶层解析 projectId（兼容两种命名）。"""
    payload = env.payload or {}
    return payload.get("projectId") or payload.get("project_id") or env.projectId


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
    }


def _asset_row_to_dict(row: Any) -> dict[str, Any]:
    """把 ``source_assets`` 行转为可序列化的 dict（JSON 列解析为对象）。"""
    return {
        "id": str(row["id"]),
        "project_id": str(row["project_id"]),
        "kind": str(row["kind"]),
        "local_uri": str(row["local_uri"]),
        "original_uri": (
            str(row["original_uri"]) if row["original_uri"] is not None else None
        ),
        "content_hash": str(row["content_hash"]),
        "rights_declaration": (
            str(row["rights_declaration"])
            if row["rights_declaration"] is not None
            else None
        ),
        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        "created_at": str(row["created_at"]),
    }


def _version_row_to_dict(row: Any) -> dict[str, Any]:
    """把 ``content_versions`` 行转为可序列化的 dict（JSON 列解析为对象）。"""
    return {
        "id": str(row["id"]),
        "project_id": str(row["project_id"]),
        "parent_version_id": (
            str(row["parent_version_id"])
            if row["parent_version_id"] is not None
            else None
        ),
        "content_type": str(row["content_type"]),
        "content": str(row["content"]),
        "content_hash": str(row["content_hash"]),
        "producer": json.loads(row["producer"]) if row["producer"] else {},
        "created_at": str(row["created_at"]),
    }


def _job_row_to_dict(row: Any) -> dict[str, Any]:
    """把 ``jobs`` 行转为可序列化的 dict（JSON 列解析为对象）。"""
    return {
        "id": str(row["id"]),
        "job_type": str(row["job_type"]),
        "state": str(row["state"]),
        "stage": str(row["stage"]) if row["stage"] is not None else None,
        "payload": json.loads(row["payload"]) if row["payload"] else {},
        "progress": float(row["progress"]),
        "attempt_count": int(row["attempt_count"]),
        "max_attempts": int(row["max_attempts"]),
        "lease_owner": (
            str(row["lease_owner"]) if row["lease_owner"] is not None else None
        ),
        "lease_expires_at": (
            str(row["lease_expires_at"])
            if row["lease_expires_at"] is not None
            else None
        ),
        "heartbeat_at": (
            str(row["heartbeat_at"]) if row["heartbeat_at"] is not None else None
        ),
        "error_code": (
            str(row["error_code"]) if row["error_code"] is not None else None
        ),
        "result_artifact_ids": (
            json.loads(row["result_artifact_ids"]) if row["result_artifact_ids"] else []
        ),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _json_col(val: Any) -> str:
    """把 JSON 列值规整为落库 TEXT：dict/list → json.dumps，str → 原样。

    导出时 JSON 列已解析为 dict/list（见 ``_row_to_dict`` 系列），导入时
    需重新序列化为 TEXT 落库；若值已是字符串（外部构造的 bundle），直接使用。
    """
    if isinstance(val, str):
        return val
    return json.dumps(val, ensure_ascii=False)


def _collect_project_jobs(conn: Any, version_ids: list[str]) -> list[dict[str, Any]]:
    """筛出 payload 引用了项目任一 content_version 的 jobs。

    ``jobs`` 表无 ``project_id`` 列，故以 payload TEXT 子串匹配项目下任意
    version_id 作为关联启发式（W9 不深挖；version_id 为 uuid hex，子串匹配
    误命中风险极低）。项目无版本时返回空 list。
    """
    if not version_ids:
        return []
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    jobs: list[dict[str, Any]] = []
    for row in rows:
        payload_text = str(row["payload"]) if row["payload"] else "{}"
        if any(vid in payload_text for vid in version_ids):
            jobs.append(_job_row_to_dict(row))
    return jobs


def _topo_sort_versions(versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """拓扑排序版本：parent_version_id 指向的版本排在前面（FK 约束）。

    导入时 ``content_versions`` 的 ``parent_version_id`` 有自引用 FK，
    必须先插入 parent 行再插入 child 行。导出时按 ``created_at`` 排序通常
    已满足拓扑序，但此处显式排序以防御等时间戳等边界情况。
    """
    by_id: dict[str, dict[str, Any]] = {str(v["id"]): v for v in versions}
    visited: set[str] = set()
    result: list[dict[str, Any]] = []

    def visit(vid: str) -> None:
        if vid in visited or vid not in by_id:
            return
        visited.add(vid)
        v = by_id[vid]
        parent = v.get("parent_version_id")
        if parent is not None:
            visit(str(parent))
        result.append(v)

    for ver in versions:
        visit(str(ver["id"]))
    return result


def _validate_bundle_names(zf: zipfile.ZipFile) -> None:
    """校验 zip 内每个成员名不含 ``..``（防路径穿越，P0 R8）。"""
    for info in zf.infolist():
        if ".." in info.filename:
            raise DispatchError(
                "INVALID_ARGUMENT",
                f"unsafe path in bundle: {info.filename}",
            )


def _new_id(prefix: str) -> str:
    """生成 ``<prefix>_<uuid4 hex>`` 形式的新主键（与 models._uid 风格一致）。"""
    return f"{prefix}_{uuid.uuid4().hex}"


def _check_bundle_path(bundle_path_str: str) -> Path:
    """校验 bundlePath 存在且为 .zip 文件，返回 Path 对象。

    同步函数（在 async handler 外做阻塞 I/O，避免 ASYNC240；与 diagnostics.py
    的 ``_collect_recent_logs`` 同模式：阻塞调用集中在 sync helper 内）。
    """
    bundle_path = Path(bundle_path_str)
    if not bundle_path.exists() or not bundle_path.is_file():
        raise DispatchError(
            "INVALID_ARGUMENT", f"bundle not found: {bundle_path_str}"
        )
    if bundle_path.suffix.lower() != ".zip":
        raise DispatchError(
            "INVALID_ARGUMENT", f"bundle must be a .zip file: {bundle_path_str}"
        )
    return bundle_path


async def _handle_export(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``ExportProject``：收集项目数据并打成 zip bundle。"""
    payload = env.payload or {}
    project_id = _resolve_project_id(env)
    if not project_id:
        raise DispatchError("INVALID_ARGUMENT", "missing projectId")

    include_assets = bool(payload.get("includeAssets", True))
    include_jobs = bool(payload.get("includeJobs", True))

    conn = deps.repos.conn
    proj_row = conn.execute(
        "SELECT * FROM content_projects WHERE id=?", (project_id,)
    ).fetchone()
    if proj_row is None:
        raise DispatchError("NOT_FOUND", f"project {project_id!r} not found")

    project_dict = _project_row_to_dict(proj_row)

    version_rows = conn.execute(
        "SELECT * FROM content_versions WHERE project_id=? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    versions = [_version_row_to_dict(r) for r in version_rows]

    if include_assets:
        asset_rows = conn.execute(
            "SELECT * FROM source_assets WHERE project_id=? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        assets = [_asset_row_to_dict(r) for r in asset_rows]
    else:
        assets = []

    if include_jobs:
        version_ids = [v["id"] for v in versions]
        jobs = _collect_project_jobs(conn, version_ids)
    else:
        jobs = []

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    home = _resolve_stepwork_home()
    exports_dir = home / _EXPORTS_DIR
    exports_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = exports_dir / f"project-{project_id}-{timestamp}.zip"

    manifest = {
        "schema_version": _SCHEMA_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "project_id": project_id,
        "versions_count": len(versions),
        "assets_count": len(assets),
        "jobs_count": len(jobs),
    }

    contents: dict[str, Any] = {
        "manifest.json": manifest,
        "project.json": project_dict,
        "versions.json": versions,
        "assets.json": assets,
    }
    if include_jobs:
        contents["jobs.json"] = jobs

    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, obj in contents.items():
            zf.writestr(name, json.dumps(obj, ensure_ascii=False, indent=2))

    size_bytes = bundle_path.stat().st_size
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        detail={
            "bundle_path": str(bundle_path),
            "size_bytes": size_bytes,
            "project_id": project_id,
            "versions_count": len(versions),
            "assets_count": len(assets),
            "jobs_count": len(jobs),
        },
    )


async def _handle_import(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 ``ImportProject``：从 zip bundle 恢复项目到当前工作区。"""
    payload = env.payload or {}
    bundle_path_str = payload.get("bundlePath") or payload.get("bundle_path")
    if not bundle_path_str:
        raise DispatchError("INVALID_ARGUMENT", "missing bundlePath")

    bundle_path = _check_bundle_path(str(bundle_path_str))

    try:
        zf = zipfile.ZipFile(bundle_path, "r")
    except zipfile.BadZipFile as e:
        raise DispatchError(
            "INVALID_ARGUMENT", f"bad zip file: {e}"
        ) from None

    with zf:
        _validate_bundle_names(zf)
        project_raw = zf.read("project.json")
        versions_raw = zf.read("versions.json")
        assets_raw = zf.read("assets.json")
        jobs_raw = (
            zf.read("jobs.json") if "jobs.json" in zf.namelist() else b"[]"
        )

    project_dict: dict[str, Any] = json.loads(project_raw)
    versions: list[dict[str, Any]] = json.loads(versions_raw)
    assets: list[dict[str, Any]] = json.loads(assets_raw)
    jobs: list[dict[str, Any]] = json.loads(jobs_raw)

    remap = bool(payload.get("remapId", True))
    id_map: dict[str, str] = {}

    def remap_own(old_id: str, prefix: str) -> str:
        """为实体自身 id 生成新 id（幂等：同 old_id 多次调用返同 new_id）。"""
        if not remap:
            return old_id
        if old_id not in id_map:
            id_map[old_id] = _new_id(prefix)
        return id_map[old_id]

    def translate_ref(old_id: str | None) -> str | None:
        """翻译外键引用：仅在 id_map 中存在时翻译，否则返 None。"""
        if old_id is None:
            return None
        if not remap:
            return old_id
        return id_map.get(old_id)

    # 预填充 id_map：先为所有实体生成新 id，确保引用翻译时映射已就绪
    old_project_id = str(project_dict["id"])
    if remap:
        for v in versions:
            remap_own(str(v["id"]), "cv")
        for a in assets:
            remap_own(str(a["id"]), "asset")
        for j in jobs:
            remap_own(str(j["id"]), "job")
    new_project_id = remap_own(old_project_id, "prj")

    # title 后缀（仅 remapId=True 时加，避免与原项目同名）
    if remap:
        short_ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        new_title = f"{str(project_dict['title'])} (imported {short_ts})"
    else:
        new_title = str(project_dict["title"])

    # current_content_version_id 翻译（version 新 id 已在 id_map 中）
    old_current_cv = project_dict.get("current_content_version_id")
    new_current_cv = translate_ref(old_current_cv if old_current_cv else None)

    repos = deps.repos
    repos.workspaces.ensure(env.workspaceId)
    conn = repos.conn

    # 插入 project（workspace_id 用 env.workspaceId）
    conn.execute(
        "INSERT INTO content_projects (id, workspace_id, title, status, "
        "brand_profile_id, current_content_version_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            new_project_id,
            env.workspaceId,
            new_title,
            str(project_dict.get("status", "active")),
            project_dict.get("brand_profile_id"),
            new_current_cv,
            str(project_dict.get("created_at")),
            str(project_dict.get("updated_at")),
        ),
    )

    # 插入 assets（project_id 用新 project_id）
    for asset in assets:
        old_aid = str(asset["id"])
        new_aid = id_map.get(old_aid, old_aid)
        conn.execute(
            "INSERT INTO source_assets (id, project_id, kind, local_uri, "
            "original_uri, content_hash, rights_declaration, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_aid,
                new_project_id,
                str(asset["kind"]),
                str(asset["local_uri"]),
                asset.get("original_uri"),
                str(asset["content_hash"]),
                asset.get("rights_declaration"),
                _json_col(asset.get("metadata", {})),
                str(asset.get("created_at")),
            ),
        )

    # 插入 versions（拓扑序：parent 先于 child；parent_version_id 用 id_map 翻译）
    for ver in _topo_sort_versions(versions):
        old_vid = str(ver["id"])
        new_vid = id_map.get(old_vid, old_vid)
        new_parent = translate_ref(ver.get("parent_version_id"))
        conn.execute(
            "INSERT INTO content_versions (id, project_id, parent_version_id, "
            "content_type, content, content_hash, producer, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_vid,
                new_project_id,
                new_parent,
                str(ver["content_type"]),
                str(ver["content"]),
                str(ver["content_hash"]),
                _json_col(ver.get("producer", {})),
                str(ver.get("created_at")),
            ),
        )

    # 插入 jobs（payload 直接拷贝，不深挖 version_id 引用 —— W9 不深挖）
    for job in jobs:
        old_jid = str(job["id"])
        new_jid = id_map.get(old_jid, old_jid)
        conn.execute(
            "INSERT INTO jobs (id, job_type, state, stage, payload, progress, "
            "attempt_count, max_attempts, lease_owner, lease_expires_at, "
            "heartbeat_at, error_code, result_artifact_ids, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_jid,
                str(job["job_type"]),
                str(job["state"]),
                job.get("stage"),
                _json_col(job.get("payload", {})),
                float(job.get("progress", 0.0)),
                int(job.get("attempt_count", 0)),
                int(job.get("max_attempts", 3)),
                job.get("lease_owner"),
                job.get("lease_expires_at"),
                job.get("heartbeat_at"),
                job.get("error_code"),
                _json_col(job.get("result_artifact_ids", [])),
                str(job.get("created_at")),
                str(job.get("updated_at")),
            ),
        )

    conn.commit()

    detail: dict[str, Any] = {
        "project_id": new_project_id,
        "imported_versions": len(versions),
        "imported_assets": len(assets),
        "imported_jobs": len(jobs),
        "id_map": id_map,
    }
    if jobs:
        detail["note"] = "jobs payload copied as-is; version_id refs not remapped"
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        detail=detail,
    )


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由 ``ExportProject`` / ``ImportProject`` 两个命令。"""
    if env.commandType == "ExportProject":
        return await _handle_export(env, deps)
    if env.commandType == "ImportProject":
        return await _handle_import(env, deps)
    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by project_io handler",
    )
