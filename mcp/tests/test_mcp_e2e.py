"""真实外部 Agent 端到端：独立进程 + stdio JSON-RPC + 真实 worker 数据库。

S6 验收清单最后一条：**至少 1 个真实外部 Agent 端到端调用成功（非 fake）**。

「非 fake」在这里是逐条可核对的，不是形容：

========  ============================================================
环节      真实的东西
========  ============================================================
客户端    ``worker/runtime/agents/mcp_client.py`` 的 ``McpStdioClient``（生产代码）
传输      stdio + 行分隔 JSON-RPC 2.0，**真子进程**（不是 in-process 直调）
服务端    ``mcp/server.py``（生产代码），经 ``python -m mcp.server`` 拉起
后端      真实 SQLite + 真实 migration + 真实 dispatch → 真实 handler
数据      由**父进程**写入、由**子进程**读回
========  ============================================================

与 ``worker/tests/fakes/`` 的分工：那些替身用来**隔离**某一层（不装 AI provider
也能测 handler），是必要的；本文件要证明的是**各层接起来真的通**，所以一层替身
都不许有。

为什么数据必须跨进程：若两端共用同一个 in-process 对象，返回什么都能自圆其说。
数据在父进程落盘、再从子进程经 JSON-RPC 回来，说明它确实进了真实 DB、被真实
handler 读出来、又被真实序列化过一趟 —— 这条链上任何一环换成替身都做不到。

同理，``test_mcp.py`` 里 monkeypatch ``run_command`` 的那种隔离测法在本文件是
**故意不用**的：它证明的是 MCP 层的信封构造，不证明后端接得通。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.agents.mcp_client import (
    McpClientError,
    McpStdioClient,
    flatten_content,
)
from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.models import ContentProject, ContentVersion

_REPO = Path(__file__).resolve().parents[2]
_MIG_DIR = _REPO / "migrations"

#: MCP 信封的 ``workspaceId`` 取自 ``build_envelope`` 的默认值，种子数据必须落在
#: 同一个工作区，否则 ``list_projects`` 按 workspaceId 过滤后读不到。
_WS = "ws-local"
_TITLE = "真实外部 Agent 端到端验收"
_SCRIPT = "第一幕：真实外部 Agent 通过 stdio 调到了 STEPWORK 的真实 handler。"


def _seed(db_path: Path) -> tuple[str, str]:
    """在**父进程**里种一份真实数据，返回 ``(project_id, version_id)``。"""
    conn = connect(str(db_path))
    try:
        run_migrations(conn, _MIG_DIR)
        repos = Repos(conn)
        # projects.insert 之前必须先 ensure 工作区，否则 FOREIGN KEY 失败
        repos.workspaces.ensure(_WS, name="真实验收工作区")
        project_id = repos.projects.insert(
            ContentProject(workspace_id=_WS, title=_TITLE)
        )
        version_id = repos.content_versions.insert(
            ContentVersion(
                project_id=project_id,
                content_type="script",
                content=_SCRIPT,
                content_hash="hash-e2e",
                producer={"kind": "agent", "name": "e2e-seed"},
            )
        )
    finally:
        conn.close()
    return project_id, version_id


def _tool_json(result: dict[str, Any]) -> Any:
    """``tools/call`` 的 content → JSON。MCP 层保证成功时是 JSON 文本。"""
    return json.loads(flatten_content(result))


async def test_real_external_agent_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一个外部进程走完整 stdio 协议，读回父进程种下的真实数据。"""
    home = tmp_path / "home"
    home.mkdir()
    project_id, version_id = _seed(home / "stepwork.db")

    # 子进程需要两件环境：1) 打开刚种下的那个库（STEPWORK_HOME）
    # 2) 无论 pytest 从哪个目录起都能 import mcp —— CI 的 worker 门禁是
    #    working-directory: worker，那里看不到仓库根。
    monkeypatch.setenv("STEPWORK_HOME", str(home))
    monkeypatch.setenv(
        "PYTHONPATH", str(_REPO) + os.pathsep + os.environ.get("PYTHONPATH", "")
    )

    async with McpStdioClient(
        [sys.executable, "-m", "mcp.server"], timeout=60.0
    ) as client:
        # ---- 握手：真协议版本、真 serverInfo ----
        info = await client.initialize()
        assert info["serverInfo"]["name"] == "stepwork-mcp"
        assert info["protocolVersion"] == "2024-11-05"

        names = [t["name"] for t in await client.list_tools()]
        assert "list_projects" in names
        assert "update_config" not in names

        # ---- 真实数据跨进程回来 ----
        projects = _tool_json(await client.call_tool("list_projects", {}))
        assert [p["id"] for p in projects["projects"]] == [project_id]
        assert projects["projects"][0]["title"] == _TITLE

        project = _tool_json(
            await client.call_tool("get_project", {"project_id": project_id})
        )
        assert project["project"]["title"] == _TITLE

        # camelCase 入参 → handler 的 _resolve_project_id 认 projectId
        versions = _tool_json(
            await client.call_tool("list_content_versions", {"project_id": project_id})
        )
        assert [v["id"] for v in versions["versions"]] == [version_id]
        assert versions["versions"][0]["preview"] == _SCRIPT

        version = _tool_json(
            await client.call_tool("get_content_version", {"version_id": version_id})
        )
        assert version["version"]["content"] == _SCRIPT

        # ---- 只读工具里最敏感的那个：配置必须是掩码视图 ----
        config = _tool_json(await client.call_tool("get_config", {}))
        assert "config" in config
        assert "resolved" in config

        jobs = _tool_json(await client.call_tool("list_jobs", {"limit": 5}))
        assert jobs["jobs"] == []  # 种子数据没建 job；关键是这条链路通了

        # ---- 安全边界：写配置的工具在真机上根本不存在 ----
        with pytest.raises(McpClientError) as e:
            await client.call_tool("update_config", {"llm": {"apiKey": "x"}})
        assert e.value.code == "MCP_CLIENT_RPC_ERROR"
        assert "unknown tool" in e.value.message
