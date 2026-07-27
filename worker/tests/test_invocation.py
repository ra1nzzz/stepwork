"""Tranche 2：Provider 调用透明（detail.invocation + audit 行）测试。

覆盖：

1. AnalyzeSource / GenerateTopic / TranscribeSource 成功 detail.invocation
   {provider, model, estimated_cost}。
2. estimated_cost 从 estimated_cost_per_1k × 实际字符量粗估；无配置时 null。
3. audit_events 落一行 event_type='provider_invocation'，payload 含
   {command, provider, model, estimated_cost}，且不含密钥。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime import ingest
from worker.runtime.audit import build_invocation
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"

_VALID_ANALYSIS: dict[str, Any] = {
    "summary": "摘要",
    "topics": ["a"],
    "sentiment": "neutral",
    "suggested_title": None,
    "suggested_tags": [],
    "key_points": [],
    "target_audience": None,
    "hook": "钩子",
    "structure": ["开场"],
    "risks": [],
    "provider": "fake-ai",
    "model": "fake-1",
    "confidence": 0.8,
}


class _CostedAIProvider:
    name = "fake-ai"
    model = "fake-1"
    estimated_cost_per_1k = 0.5

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if schema and "angles" in schema.get("properties", {}):
            return {
                "angles": [
                    {"id": "a1", "title": "t", "rationale": "r", "hook": "h"}
                ]
            }
        return dict(_VALID_ANALYSIS)


class _NoCostASRProvider:
    """无 estimated_cost_per_1k 配置的 ASR（estimated_cost 应为 null）。"""

    name = "fake-asr"

    async def transcribe(
        self, media_uri: str, opts: dict[str, Any] | None = None
    ) -> Any:
        from worker.runtime.providers.asr.base import Transcript

        return Transcript(text="转写文本", provider="fake-asr")


def _deps(**kwargs: Any) -> Deps:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    return Deps(repos=Repos(conn), ingest=ingest, **kwargs)


def _env(command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": "ws-inv",
        "payload": payload,
        "requestedAt": datetime.now(UTC).isoformat(),
    }


def _audit_rows(deps: Deps) -> list[Any]:
    return deps.repos.conn.execute(
        "SELECT * FROM audit_events WHERE event_type='provider_invocation'"
    ).fetchall()


async def test_analyze_invocation_and_audit() -> None:
    deps = _deps(ai=_CostedAIProvider())
    res = await dispatch(_env("AnalyzeSource", {"text": "素材内容"}), deps)
    assert res["ok"] is True, res.get("error")

    invocation = res["detail"]["invocation"]
    assert invocation["provider"] == "fake-ai"
    assert invocation["model"] == "fake-1"
    assert isinstance(invocation["estimated_cost"], float)
    assert invocation["estimated_cost"] > 0

    rows = _audit_rows(deps)
    assert len(rows) == 1
    row = rows[0]
    assert row["command"] == "AnalyzeSource"
    assert row["correlation_id"] == "cid-AnalyzeSource"
    payload = json.loads(row["payload"])
    assert payload["command"] == "AnalyzeSource"
    assert payload["provider"] == "fake-ai"
    assert payload["model"] == "fake-1"
    assert payload["estimated_cost"] == invocation["estimated_cost"]
    # 审计不含密钥类字段
    assert "apiKey" not in row["payload"] and "api_key" not in row["payload"]


async def test_topic_invocation_and_audit() -> None:
    deps = _deps(ai=_CostedAIProvider())
    repos = deps.repos
    from worker.runtime.models import ContentProject, ContentVersion, Workspace

    ws_id = repos.workspaces.insert(Workspace(name="ws", root_path="/t"))
    prj_id = repos.projects.insert(ContentProject(workspace_id=ws_id, title="p"))
    cv_id = repos.content_versions.insert(
        ContentVersion(
            project_id=prj_id,
            content_type="transcript",
            content="素材",
            content_hash="h",
        )
    )
    env = _env("GenerateTopic", {"source_version_id": cv_id, "count": 3})
    env["workspaceId"] = ws_id
    env["projectId"] = prj_id
    res = await dispatch(env, deps)
    assert res["ok"] is True, res.get("error")
    assert res["detail"]["invocation"]["provider"] == "fake-ai"
    assert res["detail"]["invocation"]["estimated_cost"] > 0
    assert len(_audit_rows(deps)) == 1


async def test_transcribe_invocation_null_cost() -> None:
    """无成本配置的 provider：estimated_cost=null，仍写审计行。"""
    deps = _deps(asr=_NoCostASRProvider())
    res = await dispatch(
        _env("TranscribeSource", {"local_uri": "file://a.mp4"}), deps
    )
    assert res["ok"] is True, res.get("error")
    invocation = res["detail"]["invocation"]
    assert invocation["provider"] == "fake-asr"
    assert invocation["estimated_cost"] is None
    rows = _audit_rows(deps)
    assert len(rows) == 1
    assert json.loads(rows[0]["payload"])["estimated_cost"] is None


async def test_failed_command_writes_no_invocation_audit() -> None:
    """失败命令不写 provider_invocation 审计行。"""
    deps = _deps(ai=None)
    res = await dispatch(_env("AnalyzeSource", {"text": "x"}), deps)
    assert res["ok"] is False
    assert _audit_rows(deps) == []


def test_build_invocation_estimate_math() -> None:
    class _P:
        name = "p"
        model = "m"
        estimated_cost_per_1k = 2.0

    inv = build_invocation(_P(), 500)
    assert inv == {"provider": "p", "model": "m", "estimated_cost": 1.0}

    class _NoCost:
        name = "p2"

    inv2 = build_invocation(_NoCost(), 500, model="tpl")
    assert inv2["model"] == "tpl"
    assert inv2["estimated_cost"] is None
