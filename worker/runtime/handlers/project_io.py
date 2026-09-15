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

import asyncio
import json
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime.cleanup import resolve_stepwork_home
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult

# 导出 bundle 输出目录名（$STEPWORK_HOME/exports/）
_EXPORTS_DIR: str = "exports"

# bundle schema 版本（manifest.json 中声明）
_SCHEMA_VERSION: str = "1"



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


#: 媒体成员解包后的字节上限（zip bomb 防护）。桌面单机场景 2 GB 已经远超
#: 真实素材；再大的应当走"分卷导出"而不是把整个 handler 撑爆内存。
_MAX_MEDIA_MEMBER_BYTES: int = 2 * 1024 * 1024 * 1024


def _read_bundle_json(
    zf: zipfile.ZipFile, name: str, *, expect: type
) -> Any:
    """从 bundle 里读一个 JSON 成员并做**形状校验**。

    此前直接 ``json.loads(zf.read("project.json"))``：
    - ``project.json`` 缺 → KeyError 冒到最外层变成 500，用户看不出是包坏
      了还是 worker 挂了（对比：``jobs.json`` 反而加了 ``if "jobs.json" in
      zf.namelist()`` 的保护，两条路径不对称）；
    - ``versions.json`` 是 JSON 但不是 list（比如 ``{"data": [...]}``）→ 下面
      ``for v in versions`` 迭代出字符串键 → ``v["id"]`` 崩 TypeError；
    - 元素不是 dict（``[1, 2, 3]``）→ 一样崩在 ``ver["id"]``。

    统一转成 ``DispatchError("INVALID_ARGUMENT", ...)``：明确"这个包不合格"，
    而不是 worker 内部炸。
    """
    if name not in zf.namelist():
        raise DispatchError("INVALID_ARGUMENT", f"bundle missing {name}")
    try:
        raw = zf.read(name)
    except (KeyError, zipfile.BadZipFile) as e:
        raise DispatchError(
            "INVALID_ARGUMENT", f"bundle member unreadable ({name}): {e}"
        ) from None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise DispatchError(
            "INVALID_ARGUMENT", f"bundle member not JSON ({name}): {e}"
        ) from None
    if not isinstance(value, expect):
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"bundle member {name} must be {expect.__name__}, "
            f"got {type(value).__name__}",
        )
    if expect is list:
        items: list[Any] = value  # type: ignore[assignment]
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise DispatchError(
                    "INVALID_ARGUMENT",
                    f"bundle {name}[{i}] must be object, got {type(item).__name__}",
                )
    return value


def _asset_source_path(local_uri: str) -> Path | None:
    """把 asset 的 ``local_uri`` 解析为本机存在的文件路径；不存在返回 ``None``。"""
    if not local_uri:
        return None
    raw = local_uri.removeprefix("file://")
    try:
        path = Path(raw)
        return path if path.is_file() else None
    except (OSError, ValueError):
        return None


def _restore_asset_file(
    zf: zipfile.ZipFile,
    asset: dict[str, Any],
    new_project_id: str,
    new_asset_id: str,
) -> str:
    """把 bundle 内的媒体本体落到本机，返回新的 ``local_uri``。

    bundle 未携带媒体（旧格式，或导出时源文件已缺失）时返回原 ``local_uri``，
    保持向后兼容 —— 同机导入仍然可用，跨机则与修复前行为一致。

    安全：只用归档名的最后一段拼路径，绝不直接用归档路径落盘
    （``_validate_bundle_names`` 已挡 ``..``，这里再收一层）。

    体积：解包前先看 ``info.file_size`` 是否超过
    :data:`_MAX_MEDIA_MEMBER_BYTES`（zip bomb 防线），超了拒写；写入走
    64 KB 分块 copy 而非 ``out.write(src.read())`` —— 后者会把整段媒体
    一次性读进内存再落盘，GB 级假媒体直接把 handler 撑爆。
    """
    original = str(asset.get("local_uri") or "")
    arcname = asset.get("bundle_file")
    if not arcname or not isinstance(arcname, str):
        return original
    try:
        info = zf.getinfo(arcname)
    except KeyError:
        return original
    if info.file_size > _MAX_MEDIA_MEMBER_BYTES:
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"bundle media member too large ({info.file_size} bytes, "
            f"limit {_MAX_MEDIA_MEMBER_BYTES}): {arcname}",
        )

    suffix = Path(info.filename).suffix
    dest_dir = resolve_stepwork_home() / "assets" / new_project_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{new_asset_id}{suffix}"
    try:
        with zf.open(info) as src, open(dest, "wb") as out:
            while True:
                chunk = src.read(65536)
                if not chunk:
                    break
                out.write(chunk)
    except (OSError, zipfile.BadZipFile):
        # 落盘失败不阻塞导入：数据行仍恢复，只是素材文件缺失
        return original
    return str(dest)


def _restore_asset_file_by_path(
    bundle_path_str: str,
    asset: dict[str, Any],
    new_project_id: str,
    new_asset_id: str,
) -> str:
    """:func:`_restore_asset_file` 的线程安全外壳。

    ``asyncio.to_thread`` 里跑，主线程与 worker 线程不共享 :class:`ZipFile`
    句柄（zipfile 非线程安全，句柄交叉容易出现隐性 offset 竞态）—— 每次调用
    各自 ``with zipfile.ZipFile(...)`` 开、结束即关。
    """
    try:
        with zipfile.ZipFile(bundle_path_str, "r") as zf:
            return _restore_asset_file(zf, asset, new_project_id, new_asset_id)
    except (OSError, zipfile.BadZipFile):
        # bundle 中途被替换 / 权限变了 / 媒体已损坏：与原实现一致，
        # 不阻塞导入，交给下面的 DB 事务恢复数据行。
        return str(asset.get("local_uri") or "")


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
    home = resolve_stepwork_home()
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

    # PRD-WS-004「在另一安装实例可恢复项目」：此前 bundle 只有 JSON 行数据，
    # assets.json 里的 local_uri 是**导出机的绝对路径**，换台机器导入后素材
    # 全部失效。这里把媒体本体一并打进 zip 的 assets/ 目录，并在每条 asset
    # 上记下归档内文件名（bundle_file），导入侧据此落盘并重写 local_uri。
    media_count = 0
    if include_assets:
        for asset in assets:
            src = _asset_source_path(str(asset.get("local_uri") or ""))
            if src is None:
                continue  # 文件缺失（已被清理/移动）：只带数据行，不阻塞导出
            arcname = f"assets/{asset['id']}{src.suffix}"
            asset["bundle_file"] = arcname
            media_count += 1

    def _write_bundle() -> int:
        """同步执行：整段 zip 写入 + 素材 deflate 压缩，放线程池里跑。"""
        with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, obj in contents.items():
                zf.writestr(name, json.dumps(obj, ensure_ascii=False, indent=2))
            if include_assets:
                for asset in assets:
                    # 独立命名，避免与上文构造 arcname 的 str 变量复用同名（mypy）
                    member = asset.get("bundle_file")
                    src = _asset_source_path(str(asset.get("local_uri") or ""))
                    if member and src is not None:
                        zf.write(src, str(member))
        return bundle_path.stat().st_size

    # 整段打包（含媒体 deflate 压缩 + 落盘）是同步阻塞 IO：一条几十秒的
    # 素材在事件循环里直跑 = 心跳停 / CancelJob 排队 / 并发命令全被卡住。
    size_bytes = await asyncio.to_thread(_write_bundle)
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
        project_dict = _read_bundle_json(zf, "project.json", expect=dict)
        versions = _read_bundle_json(zf, "versions.json", expect=list)
        assets = _read_bundle_json(zf, "assets.json", expect=list)
        jobs = (
            _read_bundle_json(zf, "jobs.json", expect=list)
            if "jobs.json" in zf.namelist()
            else []
        )

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

    # 先把媒体落盘这一步单独跑（bundle 无媒体时循环空转），
    # 得到 new_aid → 本机 local_uri 的映射，留给下面 INSERT 用。
    # **不共享 ZipFile 句柄跨线程**：zipfile 不是线程安全的，且主线程与
    # 线程里交替 open/close 反而更容易出隐性 bug；直接把 bundle_path 交给
    # 线程，各自开各自关。
    asset_uris: dict[str, str] = {}
    for asset in assets:
        old_aid = str(asset["id"])
        new_aid = id_map.get(old_aid, old_aid)
        # GB 级媒体落盘走 to_thread —— 之前直接在 async handler 里 copy，
        # 一条 3 分钟的片子能冻结心跳 / CancelJob 若干秒到若干分钟。
        local_uri = await asyncio.to_thread(
            _restore_asset_file_by_path,
            str(bundle_path),
            asset,
            new_project_id,
            new_aid,
        )
        asset_uris[new_aid] = local_uri

    # **一个事务包全部 4 段 INSERT**：中途任何一步失败（FK / 类型 / 唯一约束）
    # 都 rollback，绝不留下"project 已落、assets 落一半、versions 没来"的
    # 半个项目 —— 那种状态下下一次 dispatch 的 commit 会把它一起提交进库。
    # 与 :meth:`ContentVersionRepo.replace_for_version` 同一纪律，理由一致：
    # 半新半旧比全旧更危险。
    with conn:
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

        # 插入 assets：媒体已在上面 async 阶段落盘，这里只写落点 uri
        for asset in assets:
            new_aid = id_map.get(str(asset["id"]), str(asset["id"]))
            conn.execute(
                "INSERT INTO source_assets (id, project_id, kind, local_uri, "
                "original_uri, content_hash, rights_declaration, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_aid,
                    new_project_id,
                    str(asset["kind"]),
                    # PRD-WS-004：bundle 带了媒体本体就用刚落地的路径；
                    # 没带则沿用原值（旧 bundle / 导出时源文件已缺失）。
                    asset_uris.get(new_aid, str(asset.get("local_uri") or "")),
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
