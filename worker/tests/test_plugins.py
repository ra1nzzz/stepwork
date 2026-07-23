"""W8 L.29：插件系统 handler 测试。

覆盖：
1. ``ListPlugins`` 空表返回 ``{plugins: []}``。
2. ``ListPlugins`` 播种 1 条后返回 1 项，字段齐全。
3. ``GetPluginManifest`` 按 id 取 manifest。
4. ``EnablePlugin`` / ``DisablePlugin`` 切换 enabled 状态。
5. Gate：禁用所有插件后核心命令 ``ListProjects`` 仍 ``ok=True``（插件子系统
   不拖垮核心通路）。

参考 ``worker/tests/test_run_command.py`` 的 ``tmp_path`` + 真实 sqlite3 模式：
独立连接跑完 0001-0004 迁移后播种数据，再经进程内 ``dispatch`` 路由到 handler。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _env(
    command_type: str,
    payload: dict[str, Any] | None = None,
    workspace_id: str = "ws-local",
    actor_type: str = "desktop",
) -> dict[str, Any]:
    """构造一个最小合规信封 dict（对齐 ``command-envelope.schema.json``）。"""
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": actor_type, "id": f"{actor_type}-test"},
        "source": "ui",
        "workspaceId": workspace_id,
        "requestedAt": datetime.now(UTC).isoformat(),
        "payload": payload or {},
    }


def _run(raw: dict[str, Any], deps: Deps) -> dict[str, Any]:
    """在独立事件循环内经 ``dispatch`` 跑一条命令（同步测试内调用）。"""
    return asyncio.run(dispatch(raw, deps))


def _new_db(tmp_path: Path) -> tuple[sqlite3.Connection, Repos]:
    """打开一个跑完 0001-0004 迁移的临时 SQLite 库。"""
    db_path = str(tmp_path / "plugins.db")
    conn = connect(db_path)
    run_migrations(conn, _MIG_DIR)
    repos = Repos(conn)
    return conn, repos


def _insert_plugin(
    conn: sqlite3.Connection,
    *,
    pid: str = "plug_demo",
    manifest: dict[str, Any] | None = None,
    enabled: int = 0,
    status: str = "registered",
    error_message: str | None = None,
    installed_at: str | None = None,
) -> None:
    """直接 INSERT 一条 ``installed_plugins`` 行（测试播种）。"""
    if manifest is None:
        manifest = {"name": "demo", "version": "0.1.0"}
    conn.execute(
        "INSERT INTO installed_plugins "
        "(id, manifest_json, enabled, installed_at, last_loaded_at, status, error_message) "
        "VALUES (?, ?, ?, ?, NULL, ?, ?)",
        (
            pid,
            json.dumps(manifest, ensure_ascii=False),
            enabled,
            installed_at or datetime.now(UTC).isoformat(),
            status,
            error_message,
        ),
    )
    conn.commit()


def test_list_plugins_empty(tmp_path: Path) -> None:
    """空表 ``ListPlugins`` 返回 ``{plugins: []}``。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        res = _run(_env("ListPlugins"), deps)
        assert res["ok"] is True
        assert res["detail"]["plugins"] == []
    finally:
        conn.close()


def test_list_plugins_with_one(tmp_path: Path) -> None:
    """播种 1 条插件后 ``ListPlugins`` 返回 1 项，字段齐全。"""
    conn, repos = _new_db(tmp_path)
    try:
        _insert_plugin(
            conn, pid="plug_a", manifest={"name": "alpha", "version": "1.0.0"}
        )
        deps = Deps(repos=repos)
        res = _run(_env("ListPlugins"), deps)
        assert res["ok"] is True
        plugins = res["detail"]["plugins"]
        assert len(plugins) == 1
        p = plugins[0]
        assert p["id"] == "plug_a"
        assert p["enabled"] is False
        assert p["status"] == "registered"
        assert p["manifest"] == {"name": "alpha", "version": "1.0.0"}
        assert isinstance(p["installed_at"], str)
    finally:
        conn.close()


def test_get_plugin_manifest(tmp_path: Path) -> None:
    """``GetPluginManifest`` 按 id 取出 manifest（兼容 pluginId / plugin_id）。"""
    conn, repos = _new_db(tmp_path)
    try:
        _insert_plugin(
            conn,
            pid="plug_b",
            manifest={"name": "beta", "version": "2.0.0", "permissions": []},
        )
        deps = Deps(repos=repos)
        # pluginId 命名
        res = _run(_env("GetPluginManifest", {"pluginId": "plug_b"}), deps)
        assert res["ok"] is True
        plugin = res["detail"]["plugin"]
        assert plugin["id"] == "plug_b"
        assert plugin["manifest"]["name"] == "beta"
        assert plugin["manifest"]["version"] == "2.0.0"
        # plugin_id 命名（兼容）
        res2 = _run(_env("GetPluginManifest", {"plugin_id": "plug_b"}), deps)
        assert res2["ok"] is True
        assert res2["detail"]["plugin"]["id"] == "plug_b"
    finally:
        conn.close()


def test_enable_disable_plugin(tmp_path: Path) -> None:
    """Enable 后 enabled=True、status='registered'；Disable 后 enabled=False。"""
    conn, repos = _new_db(tmp_path)
    try:
        _insert_plugin(conn, pid="plug_c", manifest={"name": "gamma"}, enabled=0)
        deps = Deps(repos=repos)

        # Enable
        res_e = _run(_env("EnablePlugin", {"pluginId": "plug_c"}), deps)
        assert res_e["ok"] is True
        enabled_plugin = res_e["detail"]["plugin"]
        assert enabled_plugin["enabled"] is True
        assert enabled_plugin["status"] == "registered"

        # Disable
        res_d = _run(_env("DisablePlugin", {"pluginId": "plug_c"}), deps)
        assert res_d["ok"] is True
        disabled_plugin = res_d["detail"]["plugin"]
        assert disabled_plugin["enabled"] is False
    finally:
        conn.close()


def test_disable_plugin_then_core_command_still_works(tmp_path: Path) -> None:
    """Gate：禁用所有插件后核心命令 ``ListProjects`` 仍 ``ok=True``。

    验证插件子系统（即使全部禁用）不会拖垮核心命令通路——W8 L.29 核心 Gate。
    """
    conn, repos = _new_db(tmp_path)
    try:
        # 播种 2 个插件并全部禁用
        _insert_plugin(conn, pid="plug_d1", manifest={"name": "d1"}, enabled=1)
        _insert_plugin(conn, pid="plug_d2", manifest={"name": "d2"}, enabled=1)
        deps = Deps(repos=repos)

        _run(_env("DisablePlugin", {"pluginId": "plug_d1"}), deps)
        _run(_env("DisablePlugin", {"pluginId": "plug_d2"}), deps)

        # 确保工作区存在（ListProjects 按 workspace_id 过滤 content_projects）
        repos.workspaces.ensure("ws-local")

        # 核心命令 ListProjects 仍 ok=True
        res = _run(_env("ListProjects", workspace_id="ws-local"), deps)
        assert res["ok"] is True
        assert isinstance(res["detail"]["projects"], list)
    finally:
        conn.close()
