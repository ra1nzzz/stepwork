"""插件系统 handler（W8 L.29）。

只读查询 + 启停状态管理。**不真起子进程**（ADR-009 Plugin Isolated Process
是 V0.2 范围；V0.1 仅落库状态切换）。

四个命令：

- ``ListPlugins``：列出所有已安装插件（按 ``installed_at DESC``）。
- ``GetPluginManifest``：按 id 取单个插件 manifest（兼容 ``payload.pluginId`` /
  ``payload.plugin_id`` 两种命名）。
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
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult


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


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """路由 ``ListPlugins`` / ``GetPluginManifest`` / ``EnablePlugin`` / ``DisablePlugin``。"""
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
