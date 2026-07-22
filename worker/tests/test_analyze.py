"""Batch 2：AnalyzeSource handler（经 Command Bus 端到端）测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from worker.runtime import ingest
from worker.runtime.analysis.report import AnalysisReport
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentProject, ContentVersion, Workspace

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"

_VALID: dict[str, Any] = {
    "summary": "本期聊自动化工作流。",
    "topics": ["自动化"],
    "sentiment": "positive",
    "suggested_title": "自动化工作流入门",
    "suggested_tags": ["自动化"],
    "key_points": ["导入", "转写"],
    "target_audience": "创作者",
    "provider": "fake",
    "model": "fake-1",
    "confidence": 0.9,
}


class _FakeAIProvider:
    name = "fake-ai"
    model = "fake-1"
    estimated_cost_per_1k = 0.0

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return dict(_VALID)


class _BadAIProvider:
    name = "bad-ai"
    model = "bad-1"
    estimated_cost_per_1k = 0.0

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # 缺 required 字段，应触发 schema 校验失败
        return {"summary": "只有摘要"}


def _deps(ai: Any) -> Deps:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    return Deps(repos=Repos(c), ingest=ingest, asr=None, ai=ai)


def _env(
    command_type: str,
    payload: dict[str, Any],
    workspace_id: str = "ws-1",
    project_id: str | None = None,
) -> dict[str, Any]:
    return {
        "commandId": "cmd-a",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": workspace_id,
        "projectId": project_id,
        "payload": payload,
        "requestedAt": "2026-07-21T00:00:00+00:00",
    }


async def test_dispatch_analyze_by_text() -> None:
    deps = _deps(_FakeAIProvider())
    res = await dispatch(_env("AnalyzeSource", {"text": "素材转写内容……"}), deps)
    assert res["ok"] is True
    assert len(res["artifact_ids"]) == 1

    cv_id = res["artifact_ids"][0]
    row = deps.repos.conn.execute(
        "SELECT content_type, content FROM content_versions WHERE id=?", (cv_id,)
    ).fetchone()
    assert row is not None
    assert row["content_type"] == "analysis"
    report = AnalysisReport.model_validate_json(row["content"])
    assert report.sentiment == "positive"


async def test_dispatch_analyze_by_transcript_version() -> None:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos = Repos(c)
    ws_id = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj_id = repos.projects.insert(ContentProject(workspace_id=ws_id, title="p"))
    tv = ContentVersion(
        project_id=prj_id,
        content_type="transcript",
        content="转写文本",
        content_hash=hashlib.sha256(b"x").hexdigest(),
    )
    tv_id = repos.content_versions.insert(tv)

    deps = Deps(repos=repos, ingest=ingest, asr=None, ai=_FakeAIProvider())
    res = await dispatch(
        _env("AnalyzeSource", {"transcript_version_id": tv_id}, ws_id, prj_id),
        deps,
    )
    assert res["ok"] is True
    assert res["detail"]["sentiment"] == "positive"


async def test_analyze_without_provider_fails() -> None:
    deps = _deps(None)
    res = await dispatch(_env("AnalyzeSource", {"text": "x"}), deps)
    assert res["ok"] is False
    assert "UNAVAILABLE" in (res.get("error") or "")


async def test_analyze_invalid_report_fails() -> None:
    deps = _deps(_BadAIProvider())
    res = await dispatch(_env("AnalyzeSource", {"text": "x"}), deps)
    assert res["ok"] is False
    assert "ANALYSIS_FAILED" in (res.get("error") or "")
