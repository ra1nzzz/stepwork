"""Tranche 2：SaveAnalysis 版本链 + 分析 schema 扩展（hook/structure/risks）测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime import ingest
from worker.runtime.analysis.prompt import build_analysis_prompt
from worker.runtime.analysis.report import parse_analysis_report
from worker.runtime.analysis.schema import ANALYSIS_SCHEMA
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _deps() -> Deps:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    return Deps(repos=Repos(conn), ingest=ingest)


def _env(
    command_type: str,
    payload: dict[str, Any] | None = None,
    workspace_id: str = "ws-sa",
    project_id: str | None = None,
) -> dict[str, Any]:
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": workspace_id,
        "projectId": project_id,
        "payload": payload or {},
        "requestedAt": datetime.now(UTC).isoformat(),
    }


async def test_save_analysis_version_chain() -> None:
    """两次保存形成 parent 链；content_type='analysis'。"""
    deps = _deps()
    first = await dispatch(
        _env("SaveAnalysis", {"content": '{"summary": "v1"}'}), deps
    )
    assert first["ok"] is True, first.get("error")
    v1 = first["detail"]["version_id"]

    second = await dispatch(
        _env(
            "SaveAnalysis",
            {"content": '{"summary": "v2"}', "parentVersionId": v1},
        ),
        deps,
    )
    assert second["ok"] is True
    v2 = second["detail"]["version_id"]
    assert second["detail"]["parent"] == v1

    row = deps.repos.conn.execute(
        "SELECT content_type, parent_version_id, content "
        "FROM content_versions WHERE id=?",
        (v2,),
    ).fetchone()
    assert row["content_type"] == "analysis"
    assert row["parent_version_id"] == v1
    assert "v2" in row["content"]


async def test_save_analysis_rejects_invalid_json() -> None:
    deps = _deps()
    res = await dispatch(_env("SaveAnalysis", {"content": "{not json"}), deps)
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]


async def test_save_analysis_missing_content() -> None:
    deps = _deps()
    res = await dispatch(_env("SaveAnalysis", {}), deps)
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]


async def test_save_analysis_bad_parent() -> None:
    deps = _deps()
    res = await dispatch(
        _env(
            "SaveAnalysis",
            {"content": "{}", "parentVersionId": "cv_nope"},
        ),
        deps,
    )
    assert res["ok"] is False
    assert "NOT_FOUND" in res["error"]


def test_analysis_schema_requires_hook_structure_risks() -> None:
    """ANALYSIS_SCHEMA required 含新三字段（PRD-ANA-002/004）。"""
    for field in ("hook", "structure", "risks"):
        assert field in ANALYSIS_SCHEMA["properties"]
        assert field in ANALYSIS_SCHEMA["required"]


def test_analysis_schema_json_file_in_sync() -> None:
    """schemas/analysis.schema.json 与 ANALYSIS_SCHEMA dict 同步。"""
    import json

    path = Path(__file__).resolve().parents[2] / "schemas" / "analysis.schema.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == ANALYSIS_SCHEMA


def test_parse_report_with_new_fields() -> None:
    report = parse_analysis_report(
        {
            "summary": "s",
            "topics": [],
            "sentiment": "neutral",
            "suggested_title": None,
            "suggested_tags": [],
            "key_points": [],
            "target_audience": None,
            "hook": "开头钩子",
            "structure": ["开场", "论证", "收尾"],
            "risks": ["数据未核验"],
            "provider": "p",
            "model": "m",
            "confidence": 0.5,
        }
    )
    assert report.hook == "开头钩子"
    assert report.structure == ["开场", "论证", "收尾"]
    assert report.risks == ["数据未核验"]


def test_prompt_mentions_new_fields() -> None:
    prompt = build_analysis_prompt({"text": "素材"})
    assert "hook" in prompt
    assert "structure" in prompt
    assert "risks" in prompt
