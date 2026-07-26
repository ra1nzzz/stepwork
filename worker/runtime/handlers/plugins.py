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
    return {
        "id": str(row["id"]),
        "enabled": bool(row["enabled"]),
        "status": status,
        "manifest": manifest,
        "installed_at": str(row["installed_at"]),
        "error_message": error_message,
    }


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
            "UPDATE installed_plugins SET enabled=1, status='registered' WHERE id=?",
            (pid,),
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

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by plugins handler",
    )
