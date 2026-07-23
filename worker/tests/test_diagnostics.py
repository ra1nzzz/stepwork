"""W8 L.32：诊断包导出 handler 测试。

覆盖：
1. ``test_export_diagnostics_bundle_default_desensitized``：默认导出脱敏，
   ``desensitized=True``、bundle 文件存在、``size_bytes > 0``。
2. ``test_diagnostics_bundle_no_plaintext_secrets``（**P0 Gate**）：在 workspace
   settings 写入含 ``apiKey: "sk-real-secret-12345"`` 的配置，导出后解压，
   grep bundle 内容断言不含明文密钥。
3. ``test_export_diagnostics_bundle_explicit_desensitize_false``：传
   ``desensitize: false``，断言 ``desensitized=False``（测试环境无密钥，仅验证开关）。

参考 ``worker/tests/test_provenance.py`` 的 ``tmp_path`` + 真实 sqlite3 模式：
独立连接跑完 0001-0004 迁移后播种数据，再经进程内 ``dispatch`` 路由到 handler。
"""

from __future__ import annotations

import asyncio
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

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
    db_path = str(tmp_path / "diagnostics.db")
    conn = connect(db_path)
    run_migrations(conn, _MIG_DIR)
    repos = Repos(conn)
    return conn, repos


def _read_bundle_contents(bundle_path: str) -> dict[str, str]:
    """解压 bundle 并返回 ``{filename: content}`` 映射。"""
    contents: dict[str, str] = {}
    with zipfile.ZipFile(bundle_path, "r") as zf:
        for name in zf.namelist():
            contents[name] = zf.read(name).decode("utf-8", errors="replace")
    return contents


def test_export_diagnostics_bundle_default_desensitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认导出脱敏：desensitized=True、bundle 文件存在、size_bytes > 0。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    conn, repos = _new_db(tmp_path)
    try:
        repos.workspaces.ensure("ws-local")
        deps = Deps(repos=repos)
        res = _run(_env("ExportDiagnosticsBundle"), deps)
        assert res["ok"] is True
        detail = res["detail"]
        assert detail["desensitized"] is True
        bundle_path = Path(detail["bundle_path"])
        assert bundle_path.exists()
        assert detail["size_bytes"] > 0
        assert isinstance(detail["contents"], list)
        assert len(detail["contents"]) > 0
    finally:
        conn.close()


def test_diagnostics_bundle_no_plaintext_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0 Gate：含密钥的配置导出后 bundle 内无明文密钥。

    在 workspace settings 写入 ``apiKey: "sk-real-secret-12345"``，导出 bundle
    后解压所有文件，断言明文密钥不出现在任何文件中；同时验证密钥已被掩码
    （``config.json`` 中出现 ``••••``）。
    """
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    conn, repos = _new_db(tmp_path)
    try:
        ws = repos.workspaces.ensure("ws-local")
        # 写入含明文密钥的配置（模拟误入库的密钥场景）
        repos.workspaces.update_settings(
            ws.id,
            {
                "llm": {
                    "provider": "cloud",
                    "apiKey": "sk-real-secret-12345",
                    "model": "step-3.7",
                }
            },
        )
        deps = Deps(repos=repos)
        res = _run(_env("ExportDiagnosticsBundle"), deps)
        assert res["ok"] is True
        assert res["detail"]["desensitized"] is True

        # 解压 bundle，逐文件 grep 明文密钥
        contents = _read_bundle_contents(res["detail"]["bundle_path"])
        for filename, content in contents.items():
            assert "sk-real-secret-12345" not in content, (
                f"plaintext secret found in {filename}"
            )
        # 验证密钥已被掩码（config.json 中应出现 ••••）
        config_content = contents.get("config.json", "")
        assert "••••" in config_content
    finally:
        conn.close()


def test_export_diagnostics_bundle_explicit_desensitize_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """传 desensitize: false，断言 desensitized=False（测试环境无密钥，仅验证开关）。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    conn, repos = _new_db(tmp_path)
    try:
        repos.workspaces.ensure("ws-local")
        deps = Deps(repos=repos)
        res = _run(
            _env("ExportDiagnosticsBundle", {"desensitize": False}),
            deps,
        )
        assert res["ok"] is True
        assert res["detail"]["desensitized"] is False
        bundle_path = Path(res["detail"]["bundle_path"])
        assert bundle_path.exists()
    finally:
        conn.close()
