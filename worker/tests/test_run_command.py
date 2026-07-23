"""W7 Phase 3：``run_command`` 集成测试（进程内，绝不 spawn 子进程）。

覆盖：
1. 网关测试：``GetConfig``（desktop actor）经 ``run_command`` 返回含 ``ok`` 的 dict。
2. 权限测试：相同命令但 ``actor=agent`` → ``ok=False``（配置命令被拒绝）。
3. 查询测试：``ListProjects``（desktop actor）→ ``ok=True`` 且 ``detail.projects`` 为 list。

注意：Windows 沙箱内 asyncio proactor + headless 下 ``python -m worker.runtime``
子进程无法启动，因此所有调用均经 ``asyncio.run(run_command(...))`` 在进程内完成。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.app import build_envelope, run_command

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _run(raw: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """在独立事件循环内运行 ``run_command``（同步测试内调用）。"""
    return asyncio.run(run_command(raw, **kwargs))


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """把 ``STEPWORK_HOME`` 指向临时目录，避免触碰真实用户主目录。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))


def test_run_command_get_config_gate() -> None:
    """网关：desktop actor 的 GetConfig 应到达 handler 并返回含 ok 的 dict。"""
    env = build_envelope(
        command_type="GetConfig",
        source="ui",
        actor_type="desktop",
        workspace_id="ws-local",
    )
    result = _run(env)
    assert isinstance(result, dict)
    assert "ok" in result


def test_run_command_update_config_agent_rejected() -> None:
    """权限：agent actor 发写配置 UpdateConfig 仍应被拒绝（三角色 P0 安全模型）。"""
    env = build_envelope(
        command_type="UpdateConfig",
        source="ui",
        actor_type="agent",
        workspace_id="ws-local",
        payload={"llm": {}},
    )
    result = _run(env)
    assert result["ok"] is False


def test_run_command_get_config_agent_allowed() -> None:
    """读配置 GetConfig 返回掩码，对任何合法 actor（含 agent）开放。

    修复：原总线把 GetConfig 与 UpdateConfig 一并限制到 {user,desktop}，
    会击穿 MCP（actor=agent）读配置的真实路径。现仅写配置受白名单限制。
    """
    env = build_envelope(
        command_type="GetConfig",
        source="ui",
        actor_type="agent",
        workspace_id="ws-local",
    )
    result = _run(env)
    assert result["ok"] is True


def test_run_command_list_projects(tmp_path: Path) -> None:
    """查询：ListProjects 经进程内 run_command 返回 ok 且 projects 为 list。

    先在独立连接中播种一个项目（同一个文件库），再经 run_command 读取，
    验证 handler 的只读 SELECT 通路。
    """
    from worker.runtime.db.connection import connect
    from worker.runtime.db.migrations import run_migrations
    from worker.runtime.db.repos import Repos
    from worker.runtime.models import ContentProject

    db_path = str(tmp_path / "query.db")
    conn = connect(db_path)
    run_migrations(conn, _MIG_DIR)
    repos = Repos(conn)
    repos.workspaces.ensure("ws-local")
    repos.projects.insert(ContentProject(workspace_id="ws-local", title="demo"))
    conn.close()

    env = build_envelope(
        command_type="ListProjects",
        source="ui",
        actor_type="desktop",
        workspace_id="ws-local",
    )
    result = _run(env, db_path=db_path)
    assert result["ok"] is True
    projects = result["detail"]["projects"]
    assert isinstance(projects, list)
    assert any(p["title"] == "demo" for p in projects)


def test_three_clients_produce_equivalent_command_result(tmp_path: Path) -> None:
    """Gate 测试：同命令经 CLI / UI(mock) / MCP 产出**相同 CommandResult**。

    W7_PLAN §10 核心 Gate。同一只读查询（GetConfig）经三个 source 构造
    信封后送入 run_command，断言 detail 字段一致（除 commandId/requestedAt
    等不可比字段外）。
    """
    from worker.runtime.db.connection import connect
    from worker.runtime.db.migrations import run_migrations
    from worker.runtime.db.repos import Repos

    db_path = str(tmp_path / "three_clients.db")
    conn = connect(db_path)
    run_migrations(conn, _MIG_DIR)
    repos = Repos(conn)
    repos.workspaces.ensure("ws-local")
    conn.close()

    def _result_for(source: str, actor_type: str) -> dict[str, Any]:
        env = build_envelope(
            command_type="GetConfig",
            source=source,
            actor_type=actor_type,
            workspace_id="ws-local",
        )
        return _run(env, db_path=db_path)

    cli_res = _result_for("cli", "desktop")
    ui_res = _result_for("ui", "desktop")
    mcp_res = _result_for("mcp", "agent")

    # 三端都成功
    assert cli_res["ok"] is True and ui_res["ok"] is True and mcp_res["ok"] is True
    # detail（掩码配置视图）一致 —— 三端一致性 Gate 的字面断言
    # （commandId/requestedAt 等不可比字段不在 CommandResult 内）
    assert cli_res["detail"] == ui_res["detail"] == mcp_res["detail"]
    # error 字段三端一致（均无错误）
    assert cli_res["error"] == ui_res["error"] == mcp_res["error"] is None

