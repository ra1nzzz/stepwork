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

# Tranche 1：InstallPlugin 测试用的示例插件目录（仓库自带）
_EXAMPLE_PLUGIN_DIR = (
    Path(__file__).resolve().parents[2] / "plugins" / "examples" / "dummy-ai-provider"
)


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


def test_install_plugin_example_manifest(tmp_path: Path) -> None:
    """安装示例插件目录 → 落库 enabled=0、status='installed'、manifest 完整。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        res = _run(_env("InstallPlugin", {"path": str(_EXAMPLE_PLUGIN_DIR)}), deps)
        assert res["ok"] is True, res.get("error")
        plugin = res["detail"]["plugin"]
        assert plugin["id"] == "dummy-ai-provider"
        assert plugin["enabled"] is False
        assert plugin["status"] == "installed"
        assert plugin["manifest"]["apiVersion"] == "1"
        assert plugin["manifest"]["permissions"] == ["ai:complete"]
        # DB 行确实是 enabled=0
        row = conn.execute(
            "SELECT enabled, status FROM installed_plugins WHERE id=?",
            ("dummy-ai-provider",),
        ).fetchone()
        assert row is not None
        assert int(row["enabled"]) == 0
        assert str(row["status"]) == "installed"
    finally:
        conn.close()


def test_install_plugin_incompatible_api_version(tmp_path: Path) -> None:
    """apiVersion 主版本 != 1 → INCOMPATIBLE_API_VERSION（PRD-PLG-001）。"""
    conn, repos = _new_db(tmp_path)
    try:
        plugin_dir = tmp_path / "bad-api-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "bad-api-plugin",
                    "name": "Bad API",
                    "version": "0.1.0",
                    "apiVersion": "2.0",
                    "permissions": [],
                }
            ),
            encoding="utf-8",
        )
        deps = Deps(repos=repos)
        res = _run(_env("InstallPlugin", {"path": str(plugin_dir)}), deps)
        assert res["ok"] is False
        assert "INCOMPATIBLE_API_VERSION" in res["error"]
        # 不落库
        row = conn.execute(
            "SELECT id FROM installed_plugins WHERE id=?", ("bad-api-plugin",)
        ).fetchone()
        assert row is None
    finally:
        conn.close()


def test_install_plugin_missing_manifest(tmp_path: Path) -> None:
    """目录无 manifest.json → INVALID_ARGUMENT。"""
    conn, repos = _new_db(tmp_path)
    try:
        empty_dir = tmp_path / "no-manifest"
        empty_dir.mkdir()
        deps = Deps(repos=repos)
        res = _run(_env("InstallPlugin", {"path": str(empty_dir)}), deps)
        assert res["ok"] is False
        assert "INVALID_ARGUMENT" in res["error"]
    finally:
        conn.close()


def test_install_plugin_missing_required_fields(tmp_path: Path) -> None:
    """manifest 缺必填字段（permissions）→ INVALID_ARGUMENT。"""
    conn, repos = _new_db(tmp_path)
    try:
        plugin_dir = tmp_path / "incomplete-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "manifest.json").write_text(
            json.dumps(
                {"id": "incomplete", "name": "x", "version": "0.1.0", "apiVersion": 1}
            ),
            encoding="utf-8",
        )
        deps = Deps(repos=repos)
        res = _run(_env("InstallPlugin", {"path": str(plugin_dir)}), deps)
        assert res["ok"] is False
        assert "INVALID_ARGUMENT" in res["error"]
        assert "permissions" in res["error"]
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


# ----- PRD-PLG-002：安装前显示所有权限 -----


def test_preview_manifest_shows_permissions_without_installing(
    tmp_path: Path,
) -> None:
    """预览只读取校验、不写库 —— 这是「安装前授权」环节的前提。

    此前安装流是「选目录 → 直接 InstallPlugin → 刷新」，权限列表在**装完
    之后**才展示，等于没有安装前确认。
    """
    conn, repos = _new_db(tmp_path)
    try:
        plugin_dir = tmp_path / "preview-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "preview-plugin",
                    "name": "预览插件",
                    "version": "1.0.0",
                    "apiVersion": "1.0",
                    "permissions": ["read:project", "write:export"],
                }
            ),
            encoding="utf-8",
        )
        deps = Deps(repos=repos)
        res = _run(_env("PreviewPluginManifest", {"path": str(plugin_dir)}), deps)
        assert res["ok"] is True, res.get("error")
        assert res["detail"]["permissions"] == ["read:project", "write:export"]
        assert res["detail"]["manifest"]["id"] == "preview-plugin"
        assert res["detail"]["already_installed"] is False
        # 关键：预览绝不写库
        n = conn.execute("SELECT COUNT(*) n FROM installed_plugins").fetchone()["n"]
        assert n == 0
    finally:
        conn.close()


def test_preview_manifest_reports_already_installed(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        plugin_dir = tmp_path / "installed-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "installed-plugin",
                    "name": "已装",
                    "version": "1.0.0",
                    "apiVersion": "1.0",
                    "permissions": [],
                }
            ),
            encoding="utf-8",
        )
        deps = Deps(repos=repos)
        _run(_env("InstallPlugin", {"path": str(plugin_dir)}), deps)
        res = _run(_env("PreviewPluginManifest", {"path": str(plugin_dir)}), deps)
        assert res["ok"] is True
        assert res["detail"]["already_installed"] is True
    finally:
        conn.close()


def test_preview_manifest_rejects_incompatible_before_install(
    tmp_path: Path,
) -> None:
    """不兼容插件在预览阶段就被拒，用户根本走不到安装。"""
    conn, repos = _new_db(tmp_path)
    try:
        plugin_dir = tmp_path / "bad-preview"
        plugin_dir.mkdir()
        (plugin_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "id": "bad-preview",
                    "name": "不兼容",
                    "version": "1.0.0",
                    "apiVersion": "9.0",
                    "permissions": [],
                }
            ),
            encoding="utf-8",
        )
        deps = Deps(repos=repos)
        res = _run(_env("PreviewPluginManifest", {"path": str(plugin_dir)}), deps)
        assert res["ok"] is False
        assert "INCOMPATIBLE_API_VERSION" in res["error"]
    finally:
        conn.close()


# ----- PRD-PLG-003 卸载 / PLG-004 信任分级 / PLG-005 健康状态 -----


def _install(tmp_path: Path, repos: Repos, pid: str, **manifest: Any) -> None:
    plugin_dir = tmp_path / pid
    plugin_dir.mkdir(exist_ok=True)
    base = {
        "id": pid,
        "name": pid,
        "version": "1.0.0",
        "apiVersion": "1.0",
        "permissions": [],
    }
    base.update(manifest)
    (plugin_dir / "manifest.json").write_text(
        json.dumps(base), encoding="utf-8"
    )
    res = _run(_env("InstallPlugin", {"path": str(plugin_dir)}), Deps(repos=repos))
    assert res["ok"] is True, res.get("error")


def test_uninstall_removes_plugin_only(tmp_path: Path) -> None:
    """PRD-PLG-003：卸载删注册表行，绝不影响项目数据。"""
    conn, repos = _new_db(tmp_path)
    try:
        _install(tmp_path, repos, "to-remove")
        # 播一条项目数据，验证卸载不碰它
        conn.execute(
            "INSERT INTO workspaces (id, name, root_path, settings, created_at) "
            "VALUES ('ws-p','w','/tmp','{}','t')"
        )
        conn.execute(
            "INSERT INTO content_projects (id, workspace_id, title, status, "
            "created_at, updated_at) VALUES ('prj-p','ws-p','t','draft','t','t')"
        )
        conn.commit()

        res = _run(
            _env("UninstallPlugin", {"pluginId": "to-remove"}), Deps(repos=repos)
        )
        assert res["ok"] is True, res.get("error")
        assert res["detail"]["uninstalled"] == "to-remove"

        n = conn.execute("SELECT COUNT(*) n FROM installed_plugins").fetchone()["n"]
        assert n == 0
        # 项目数据完整性不受影响
        projects = conn.execute(
            "SELECT COUNT(*) n FROM content_projects"
        ).fetchone()["n"]
        assert projects == 1
    finally:
        conn.close()


def test_uninstall_unknown_plugin_not_found(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        res = _run(_env("UninstallPlugin", {"pluginId": "nope"}), Deps(repos=repos))
        assert res["ok"] is False
        assert "NOT_FOUND" in res["error"]
    finally:
        conn.close()


def test_trust_tier_defaults_and_cannot_self_declare_official(
    tmp_path: Path,
) -> None:
    """PRD-PLG-004：默认 community；插件不得自封 official/verified。"""
    conn, repos = _new_db(tmp_path)
    try:
        _install(tmp_path, repos, "plain")
        _install(tmp_path, repos, "exp", trustTier="experimental")
        _install(tmp_path, repos, "liar", trustTier="official")

        res = _run(_env("ListPlugins", {}), Deps(repos=repos))
        tiers = {p["id"]: p["trust_tier"] for p in res["detail"]["plugins"]}
        assert tiers["plain"] == "community"
        assert tiers["exp"] == "experimental"
        # 自称 official 一律降级
        assert tiers["liar"] == "community"
    finally:
        conn.close()


def test_enable_records_last_loaded_at(tmp_path: Path) -> None:
    """PRD-PLG-005：启用即记加载时间（此前 last_loaded_at 恒为 NULL）。"""
    conn, repos = _new_db(tmp_path)
    try:
        _install(tmp_path, repos, "loadable")
        before = _run(_env("ListPlugins", {}), Deps(repos=repos))
        assert before["detail"]["plugins"][0]["last_loaded_at"] is None

        _run(_env("EnablePlugin", {"pluginId": "loadable"}), Deps(repos=repos))
        after = _run(_env("ListPlugins", {}), Deps(repos=repos))
        assert after["detail"]["plugins"][0]["last_loaded_at"] is not None
    finally:
        conn.close()


def test_check_health_ok_and_records_time(tmp_path: Path) -> None:
    """PRD-PLG-005：健康检查落「最近测试时间和结果」。"""
    conn, repos = _new_db(tmp_path)
    try:
        _install(tmp_path, repos, "healthy")
        res = _run(
            _env("CheckPluginHealth", {"pluginId": "healthy"}), Deps(repos=repos)
        )
        assert res["ok"] is True, res.get("error")
        assert res["detail"]["healthy"] is True
        plugin = res["detail"]["plugin"]
        assert plugin["last_checked_at"]
        assert plugin["last_check_result"] == "ok"
    finally:
        conn.close()


def test_check_health_detects_broken_manifest(tmp_path: Path) -> None:
    """manifest 损坏的已装插件，健康检查应报错并记录原因。"""
    conn, repos = _new_db(tmp_path)
    try:
        _install(tmp_path, repos, "broken")
        conn.execute(
            "UPDATE installed_plugins SET manifest_json='{not json' WHERE id='broken'"
        )
        conn.commit()

        res = _run(
            _env("CheckPluginHealth", {"pluginId": "broken"}), Deps(repos=repos)
        )
        assert res["ok"] is True
        assert res["detail"]["healthy"] is False
        assert res["detail"]["plugin"]["last_check_result"] == "error"
        assert res["detail"]["plugin"]["error_message"]
    finally:
        conn.close()
