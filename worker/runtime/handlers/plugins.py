"""插件系统 handler（W8 L.29 + Tranche 1 InstallPlugin）。

只读查询 + 安装落库 + 启停状态管理。**不真起子进程**（ADR-009 Plugin
Isolated Process 是 V0.2 范围；V0.1 仅落库状态切换）。

五个命令：

- ``ListPlugins``：列出所有已安装插件（按 ``installed_at DESC``）。
- ``GetPluginManifest``：按 id 取单个插件 manifest（兼容 ``payload.pluginId`` /
  ``payload.plugin_id`` 两种命名）。
- ``InstallPlugin``：读取 ``payload.path`` 目录下的 ``manifest.json`` →
  校验必填字段（id/name/version/apiVersion/permissions）→ apiVersion 主版本
  必须为 1（满足 PRD-PLG-001「不兼容插件不能加载」，否则
  ``INCOMPATIBLE_API_VERSION``）→ ``INSERT OR REPLACE`` 进
  ``installed_plugins``（``enabled=0``、``status='installed'``）。
- ``EnablePlugin``：``UPDATE installed_plugins SET enabled=1, status='registered'``
  （不真起进程，ADR-009 V0.2）。
- ``DisablePlugin``：``UPDATE installed_plugins SET enabled=0``（不真杀进程）。

安全模型（P0）：

- Enable / Disable 是写操作但**不需要** actor 白名单（与 ``UpdateConfig`` 不同），
  因为只是状态切换，不涉及密钥。
- ``manifest_json`` 字段是 JSON 字符串，读取时 ``json.loads`` 解析；单插件解析
  失败不影响其他——失败项的 ``status`` 展示为 ``'error'``，``error_message``
  记录原因，``manifest`` 置 ``None``。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult

# worker 支持的 plugin api 主版本（PRD-PLG-001：主版本不匹配的插件拒绝安装）
_SUPPORTED_PLUGIN_API_MAJOR = 1

# manifest.json 必填字段（缺失 → INVALID_ARGUMENT）
_REQUIRED_MANIFEST_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "version",
    "apiVersion",
    "permissions",
)


def _parse_manifest(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """解析 ``manifest_json``；失败时返回 ``(None, error_message)``。

    全程 try/except，任何异常（含 ``JSONDecodeError`` / 类型不符）都被吸收为
    ``(None, reason)``，确保单插件畸形不会击垮整列。
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"manifest_json invalid JSON: {e}"
    except Exception as e:  # pragma: no cover - 防御性兜底
        return None, f"manifest_json parse failed: {e}"
    if not isinstance(parsed, dict):
        return None, f"manifest_json is not an object: {type(parsed).__name__}"
    return parsed, None


#: PRD-PLG-004 信任分级。official/verified 由分发方背书，community 为默认，
#: experimental 表示作者自述实验性。manifest 里非法取值一律降级为 community
#: （绝不让插件自称 official 就获得更高展示等级）。
TRUST_TIERS: tuple[str, ...] = ("official", "verified", "community", "experimental")
DEFAULT_TRUST_TIER = "community"

#: 允许插件自行声明的等级：official/verified 需分发方背书，不可自封。
_SELF_DECLARABLE_TIERS: frozenset[str] = frozenset({"community", "experimental"})


def resolve_trust_tier(manifest: dict[str, Any]) -> str:
    """从 manifest 解析信任等级；不可自封 official/verified。"""
    raw = str(manifest.get("trustTier") or "").lower()
    if raw in _SELF_DECLARABLE_TIERS:
        return raw
    return DEFAULT_TRUST_TIER


def _plugin_row_to_dict(row: Any) -> dict[str, Any]:
    """把 ``installed_plugins`` 行转为可序列化的 dict。

    manifest 解析失败时 ``status`` 覆盖为 ``'error'``、``manifest`` 置 ``None``、
    ``error_message`` 记录原因；其余字段照常返回。覆盖仅发生在返回字典上，
    不写回 DB（DB 里的 ``status`` 是操作态，展示态叠加 manifest 解析结果）。
    """
    raw_manifest = str(row["manifest_json"])
    manifest, parse_err = _parse_manifest(raw_manifest)
    db_error: str | None = (
        str(row["error_message"]) if row["error_message"] is not None else None
    )
    if manifest is None:
        status = "error"
        error_message: str | None = parse_err or db_error or "manifest parse failed"
    else:
        status = str(row["status"])
        error_message = db_error
    keys = row.keys()

    def _col(name: str) -> Any:
        # 读路径对旧库（未跑 0006）缺列返回 None；写路径见 _has_trust_columns
        return row[name] if name in keys else None

    return {
        "id": str(row["id"]),
        "enabled": bool(row["enabled"]),
        "status": status,
        "manifest": manifest,
        "installed_at": str(row["installed_at"]),
        "error_message": error_message,
        # PRD-PLG-004：信任分级（UI 明确显示）
        "trust_tier": str(_col("trust_tier") or DEFAULT_TRUST_TIER),
        # PRD-PLG-005：最近加载 / 最近测试时间与结果
        "last_loaded_at": _col("last_loaded_at"),
        "last_checked_at": _col("last_checked_at"),
        "last_check_result": _col("last_check_result"),
    }


def _has_trust_columns(conn: Any) -> bool:
    """installed_plugins 是否已有 0006 的新列。

    读路径的 ``_col`` 兜底只解决「查出来没有这列」，写路径硬写新列在
    pre-0006 的库上仍会 OperationalError。真实运行时 bootstrap 一定跑过
    迁移，但测试/外部工具可能拿旧 schema 建表——这里让写路径也降级。
    """
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(installed_plugins)")}
    except Exception:  # noqa: BLE001 - 查不到就按「没有新列」保守处理
        return False
    return {"trust_tier", "last_checked_at", "last_check_result"} <= cols


def _resolve_plugin_id(env: CommandEnvelope) -> str | None:
    """从 payload 解析 pluginId（兼容 ``pluginId`` / ``plugin_id`` 两种命名）。"""
    payload = env.payload or {}
    return payload.get("pluginId") or payload.get("plugin_id")


def _api_version_major(api_version: Any) -> int | None:
    """解析 manifest ``apiVersion`` 的主版本号。

    兼容两种形状：``int``（如 ``1``）与 ``str``（如 ``"1"`` / ``"1.2"``，
    取 ``.`` 前的主版本段）。解析失败返回 ``None``。
    """
    if isinstance(api_version, bool):
        return None
    if isinstance(api_version, int):
        return api_version
    if isinstance(api_version, str):
        major = api_version.split(".", 1)[0].strip()
        try:
            return int(major)
        except ValueError:
            return None
    return None


def _load_manifest_from_dir(plugin_dir_str: str) -> dict[str, Any]:
    """读取并校验 ``<plugin_dir>/manifest.json``（同步 I/O helper）。

    Raises:
        DispatchError: 目录 / manifest.json 缺失、JSON 非法、必填字段缺失
            （``INVALID_ARGUMENT``）；apiVersion 主版本不兼容
            （``INCOMPATIBLE_API_VERSION``，PRD-PLG-001）。
    """
    plugin_dir = Path(plugin_dir_str)
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.is_file():
        raise DispatchError(
            "INVALID_ARGUMENT", f"manifest.json not found in {plugin_dir_str!r}"
        )
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise DispatchError(
            "INVALID_ARGUMENT", f"manifest.json unreadable/invalid: {e}"
        ) from None
    if not isinstance(manifest, dict):
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"manifest.json must be an object, got {type(manifest).__name__}",
        )
    missing = [f for f in _REQUIRED_MANIFEST_FIELDS if f not in manifest]
    if missing:
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"manifest.json missing required fields: {', '.join(missing)}",
        )
    major = _api_version_major(manifest["apiVersion"])
    if major != _SUPPORTED_PLUGIN_API_MAJOR:
        raise DispatchError(
            "INCOMPATIBLE_API_VERSION",
            f"plugin apiVersion {manifest['apiVersion']!r} is not compatible "
            f"(worker supports major version {_SUPPORTED_PLUGIN_API_MAJOR})",
        )
    return manifest


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由 ``ListPlugins`` / ``GetPluginManifest`` / ``InstallPlugin`` /
    ``EnablePlugin`` / ``DisablePlugin``。"""
    if env.commandType == "PreviewPluginManifest":
        # PRD-PLG-002「安装前显示所有权限」：只读取校验 manifest，不写库、
        # 不安装。此前安装流是「选目录 → 直接 InstallPlugin」，权限列表在
        # **装完之后**才显示，等于没有安装前授权环节。
        payload = env.payload or {}
        preview_dir = payload.get("path")
        if not preview_dir or not isinstance(preview_dir, str):
            raise DispatchError("INVALID_ARGUMENT", "missing path")
        manifest = _load_manifest_from_dir(preview_dir)
        permissions = manifest.get("permissions") or []
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "manifest": manifest,
                "permissions": permissions,
                "already_installed": deps.repos.conn.execute(
                    "SELECT 1 FROM installed_plugins WHERE id=?",
                    (str(manifest["id"]),),
                ).fetchone()
                is not None,
            },
        )

    if env.commandType == "InstallPlugin":
        payload = env.payload or {}
        plugin_dir = payload.get("path")
        if not plugin_dir or not isinstance(plugin_dir, str):
            raise DispatchError("INVALID_ARGUMENT", "missing path")
        manifest = _load_manifest_from_dir(plugin_dir)
        # 独立命名，避免与后续分支的 _resolve_plugin_id()（str|None）复用同名
        new_pid = str(manifest["id"])
        installed_at = datetime.now(UTC).isoformat()
        # 幂等安装：重复安装同 id 覆盖 manifest 并重置为未启用
        if _has_trust_columns(deps.repos.conn):
            deps.repos.conn.execute(
                "INSERT OR REPLACE INTO installed_plugins "
                "(id, manifest_json, enabled, installed_at, last_loaded_at, "
                "status, error_message, trust_tier) "
                "VALUES (?, ?, 0, ?, NULL, 'installed', NULL, ?)",
                (
                    new_pid,
                    json.dumps(manifest, ensure_ascii=False),
                    installed_at,
                    # PRD-PLG-004：插件不可自封 official/verified
                    resolve_trust_tier(manifest),
                ),
            )
        else:
            # 旧 schema：不写新列，功能降级但不炸
            deps.repos.conn.execute(
                "INSERT OR REPLACE INTO installed_plugins "
                "(id, manifest_json, enabled, installed_at, last_loaded_at, "
                "status, error_message) "
                "VALUES (?, ?, 0, ?, NULL, 'installed', NULL)",
                (new_pid, json.dumps(manifest, ensure_ascii=False), installed_at),
            )
        deps.repos.conn.commit()
        row = deps.repos.conn.execute(
            "SELECT * FROM installed_plugins WHERE id=?", (new_pid,)
        ).fetchone()
        assert row is not None  # INSERT 刚落库，行必然存在
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"plugin": _plugin_row_to_dict(row)},
        )

    if env.commandType == "ListPlugins":
        rows = deps.repos.conn.execute(
            "SELECT * FROM installed_plugins ORDER BY installed_at DESC"
        ).fetchall()
        plugins = [_plugin_row_to_dict(r) for r in rows]
        return CommandResult(
            ok=True, commandId=env.commandId, detail={"plugins": plugins}
        )

    if env.commandType == "GetPluginManifest":
        pid = _resolve_plugin_id(env)
        if not pid:
            raise DispatchError("INVALID_ARGUMENT", "missing pluginId")
        row = deps.repos.conn.execute(
            "SELECT * FROM installed_plugins WHERE id=?", (pid,)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"plugin {pid!r} not found")
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"plugin": _plugin_row_to_dict(row)},
        )

    if env.commandType == "EnablePlugin":
        pid = _resolve_plugin_id(env)
        if not pid:
            raise DispatchError("INVALID_ARGUMENT", "missing pluginId")
        # 不真起子进程（ADR-009 V0.2 范围）；仅切换 DB 状态。
        cur = deps.repos.conn.execute(
            # PRD-PLG-005：启用即记一次加载时间（此前 last_loaded_at 恒为 NULL）
            "UPDATE installed_plugins SET enabled=1, status='registered', "
            "last_loaded_at=? WHERE id=?",
            (datetime.now(UTC).isoformat(), pid),
        )
        deps.repos.conn.commit()
        if cur.rowcount == 0:
            raise DispatchError("NOT_FOUND", f"plugin {pid!r} not found")
        row = deps.repos.conn.execute(
            "SELECT * FROM installed_plugins WHERE id=?", (pid,)
        ).fetchone()
        assert row is not None  # UPDATE 已命中，行必然存在
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"plugin": _plugin_row_to_dict(row)},
        )

    if env.commandType == "DisablePlugin":
        pid = _resolve_plugin_id(env)
        if not pid:
            raise DispatchError("INVALID_ARGUMENT", "missing pluginId")
        # 不真杀子进程（V0.2 范围）；仅切换 DB 状态。
        cur = deps.repos.conn.execute(
            "UPDATE installed_plugins SET enabled=0 WHERE id=?", (pid,)
        )
        deps.repos.conn.commit()
        if cur.rowcount == 0:
            raise DispatchError("NOT_FOUND", f"plugin {pid!r} not found")
        row = deps.repos.conn.execute(
            "SELECT * FROM installed_plugins WHERE id=?", (pid,)
        ).fetchone()
        assert row is not None  # UPDATE 已命中，行必然存在
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"plugin": _plugin_row_to_dict(row)},
        )

    if env.commandType == "UninstallPlugin":
        # PRD-PLG-003「启用、禁用、升级和卸载 · 不影响项目数据完整性」：
        # 此前只有装/启/停，**没有卸载**。卸载只删注册表行，绝不碰
        # content_versions / source_assets 等项目数据。
        pid = _resolve_plugin_id(env)
        if not pid:
            raise DispatchError("INVALID_ARGUMENT", "missing pluginId")
        cur = deps.repos.conn.execute(
            "DELETE FROM installed_plugins WHERE id=?", (pid,)
        )
        deps.repos.conn.commit()
        if cur.rowcount == 0:
            raise DispatchError("NOT_FOUND", f"plugin {pid!r} not found")
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"uninstalled": pid},
        )

    if env.commandType == "CheckPluginHealth":
        # PRD-PLG-005「显示最近测试时间和错误」：此前 last_loaded_at 恒为
        # NULL，且没有「测试」这个概念。这里做一次可复现的健康检查：
        # manifest 能否解析 + apiVersion 是否兼容，结果落库供 UI 展示。
        pid = _resolve_plugin_id(env)
        if not pid:
            raise DispatchError("INVALID_ARGUMENT", "missing pluginId")
        row = deps.repos.conn.execute(
            "SELECT * FROM installed_plugins WHERE id=?", (pid,)
        ).fetchone()
        if row is None:
            raise DispatchError("NOT_FOUND", f"plugin {pid!r} not found")

        # 独立命名，避免与上文 InstallPlugin 分支的 dict 变量复用同名（mypy）
        health_manifest, parse_err = _parse_manifest(str(row["manifest_json"]))
        if health_manifest is None:
            healthy, detail_msg = False, parse_err or "manifest parse failed"
        elif _api_version_major(health_manifest.get("apiVersion")) != (
            _SUPPORTED_PLUGIN_API_MAJOR
        ):
            healthy, detail_msg = False, "incompatible apiVersion"
        else:
            healthy, detail_msg = True, "ok"

        checked_at = datetime.now(UTC).isoformat()
        if _has_trust_columns(deps.repos.conn):
            deps.repos.conn.execute(
                "UPDATE installed_plugins SET last_checked_at=?, last_check_result=?, "
                "error_message=? WHERE id=?",
                (
                    checked_at,
                    "ok" if healthy else "error",
                    None if healthy else detail_msg,
                    pid,
                ),
            )
        else:
            # 旧 schema（未跑 0006）：只落 error_message，健康结果不持久化
            deps.repos.conn.execute(
                "UPDATE installed_plugins SET error_message=? WHERE id=?",
                (None if healthy else detail_msg, pid),
            )
        deps.repos.conn.commit()
        updated = deps.repos.conn.execute(
            "SELECT * FROM installed_plugins WHERE id=?", (pid,)
        ).fetchone()
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "healthy": healthy,
                "checked_at": checked_at,
                "message": detail_msg,
                "plugin": _plugin_row_to_dict(updated),
            },
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by plugins handler",
    )
