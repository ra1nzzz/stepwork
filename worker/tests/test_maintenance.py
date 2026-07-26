"""维护与可追溯类命令测试。

覆盖三处「后端有能力、用户够不到」的补齐：

- ``RunCleanup``（PRD-SRC-005）：手动触发清理（此前 cleanupMode=manual
  的用户没有任何触发入口）。
- ``ListAuditEvents``（PRD-ANA-006）：审计记录可读（此前 audit_events
  全仓只有 INSERT，重启即不可查）。
- ``ListSourceAssets`` / ``GetSourceAsset``（PRD-SRC-003）：素材可追溯
  （此前只有写入与删除）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentProject, SourceAsset, Workspace

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _setup() -> tuple[Deps, str, str]:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos = Repos(c)
    ws = repos.workspaces.insert(Workspace(name="ws-m", root_path="/tmp/m"))
    prj = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    return Deps(repos=repos, ingest=ingest), ws, prj


def _env(
    command_type: str,
    payload: dict[str, Any],
    ws: str,
    prj: str | None = None,
    *,
    actor_type: str = "user",
    source: str = "ui",
) -> dict[str, Any]:
    return {
        "commandId": "cmd-m",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": actor_type, "id": "u"},
        "source": source,
        "workspaceId": ws,
        "projectId": prj,
        "payload": payload,
        "requestedAt": "2026-07-26T00:00:00+00:00",
    }


# ----- PRD-SRC-003：素材可追溯 -----


async def test_list_and_get_source_assets() -> None:
    deps, ws, prj = _setup()
    asset_id = deps.repos.source_assets.insert_dedup(
        SourceAsset(
            project_id=prj,
            kind="video",
            local_uri="/tmp/a.mp4",
            original_uri="https://example.com/a.mp4",
            content_hash="h1",
            rights_declaration="licensed",
            metadata={"author": "某作者"},
        )
    )

    res = await dispatch(_env("ListSourceAssets", {}, ws, prj), deps)
    assert res["ok"] is True
    assets = res["detail"]["assets"]
    assert len(assets) == 1
    a = assets[0]
    # PRD-SRC-003 四要素：来源 / 作者 / 导入时间 / 权利声明
    assert a["original_uri"] == "https://example.com/a.mp4"
    assert a["author"] == "某作者"
    assert a["created_at"]
    assert a["rights_declaration"] == "licensed"

    got = await dispatch(_env("GetSourceAsset", {"assetId": asset_id}, ws, prj), deps)
    assert got["ok"] is True
    assert got["detail"]["asset"]["id"] == asset_id


async def test_get_source_asset_not_found_and_missing_arg() -> None:
    deps, ws, prj = _setup()
    res = await dispatch(_env("GetSourceAsset", {"assetId": "nope"}, ws, prj), deps)
    assert res["ok"] is False
    assert "NOT_FOUND" in res["error"]

    res2 = await dispatch(_env("GetSourceAsset", {}, ws, prj), deps)
    assert res2["ok"] is False
    assert "INVALID_ARGUMENT" in res2["error"]


async def test_list_source_assets_requires_project() -> None:
    deps, ws, _prj = _setup()
    res = await dispatch(_env("ListSourceAssets", {}, ws, None), deps)
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]


# ----- PRD-ANA-006：审计可读 -----


async def test_list_audit_events_reads_recorded_invocations() -> None:
    deps, ws, prj = _setup()
    conn = deps.repos.conn
    conn.execute(
        "INSERT INTO audit_events (id, actor, source_protocol, command, target, "
        "requested_scope, approval, result, correlation_id, timestamp, "
        "event_type, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "audit_1", "user:u", "ui", "AnalyzeSource", prj, None, None, "ok",
            "cmd-x", "2026-07-26T01:00:00+00:00", "provider_invocation",
            json.dumps({"provider": "cloud-ai", "estimated_cost": 0.12}),
        ),
    )
    conn.commit()

    res = await dispatch(_env("ListAuditEvents", {}, ws, prj), deps)
    assert res["ok"] is True
    events = res["detail"]["events"]
    assert len(events) == 1
    assert events[0]["command"] == "AnalyzeSource"
    assert events[0]["event_type"] == "provider_invocation"
    # payload 已解析为对象，费用可直接读出（重启后仍可查）
    assert events[0]["payload"]["estimated_cost"] == 0.12


async def test_list_audit_events_filters_and_validates() -> None:
    deps, ws, prj = _setup()
    res = await dispatch(
        _env("ListAuditEvents", {"eventType": "no_such_type"}, ws, prj), deps
    )
    assert res["ok"] is True
    assert res["detail"]["events"] == []

    bad = await dispatch(_env("ListAuditEvents", {"limit": 0}, ws, prj), deps)
    assert bad["ok"] is False
    assert "INVALID_ARGUMENT" in bad["error"]


# ----- PRD-SRC-005：手动清理 -----


async def test_run_cleanup_removes_stale_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    stale = tmp_dir / "leftover.part"
    stale.write_text("x", encoding="utf-8")

    deps, ws, prj = _setup()
    res = await dispatch(
        _env("RunCleanup", {"mode": "immediate"}, ws, prj), deps
    )
    assert res["ok"] is True
    assert res["detail"]["mode"] == "immediate"
    assert res["detail"]["removed"] >= 1
    assert not stale.exists()


async def test_run_cleanup_rejects_invalid_mode() -> None:
    deps, ws, prj = _setup()
    res = await dispatch(_env("RunCleanup", {"mode": "manual"}, ws, prj), deps)
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]


async def test_run_cleanup_blocked_for_agents() -> None:
    """手动清理会删文件，属 §9.1 高风险操作，Agent 不可直达。"""
    deps, ws, prj = _setup()
    res = await dispatch(
        _env("RunCleanup", {}, ws, prj, actor_type="agent", source="mcp"), deps
    )
    assert res["ok"] is False
    assert "FORBIDDEN_ACTOR" in res["error"]


# ----- PRD §14 埋点：领域事件可查 -----


async def test_domain_events_recorded_and_listable() -> None:
    """PRD §14 列出的项目创建/脚本保存/审批 事件此前完全没有留痕。"""
    deps, ws, _prj = _setup()

    created = await dispatch(
        _env("CreateProject", {"title": "埋点项目"}, ws, None), deps
    )
    assert created["ok"] is True, created.get("error")
    project_id = created["detail"]["project"]["id"]

    saved = await dispatch(
        _env("SaveScript", {"content": {"text": "正文", "title": "T"}}, ws, project_id),
        deps,
    )
    assert saved["ok"] is True, saved.get("error")

    approved = await dispatch(
        _env(
            "CreateApprovalRequest",
            {"actionType": "ExportBundle", "target": project_id},
            ws,
            project_id,
        ),
        deps,
    )
    assert approved["ok"] is True

    listed = await dispatch(_env("ListAuditEvents", {"limit": 50}, ws, project_id), deps)
    assert listed["ok"] is True
    types = {e["event_type"] for e in listed["detail"]["events"]}
    assert "project_created" in types
    assert "script_saved" in types
    assert "approval_requested" in types


async def test_audit_event_filter_by_type() -> None:
    deps, ws, _prj = _setup()
    await dispatch(_env("CreateProject", {"title": "只看创建"}, ws, None), deps)

    res = await dispatch(
        _env("ListAuditEvents", {"eventType": "project_created"}, ws, None), deps
    )
    assert res["ok"] is True
    assert res["detail"]["count"] >= 1
    assert all(e["event_type"] == "project_created" for e in res["detail"]["events"])
