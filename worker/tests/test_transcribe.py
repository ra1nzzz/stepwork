"""Batch 1：TranscribeSource handler（经 Command Bus 端到端）测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.providers.asr.local import LocalASRProvider

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _deps() -> Deps:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    return Deps(repos=Repos(c), ingest=ingest, asr=LocalASRProvider(), ai=None)


def _env(command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "commandId": "cmd-t",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": "ws-1",
        "payload": payload,
        "requestedAt": "2026-07-21T00:00:00+00:00",
    }


async def test_dispatch_transcribe_source() -> None:
    deps = _deps()
    payload: dict[str, Any] = {
        "local_uri": "file://a.mp4",
        "opts": {"duration_sec": 12},
    }
    res = await dispatch(_env("TranscribeSource", payload), deps)
    assert res["ok"] is True
    assert len(res["artifact_ids"]) == 1

    cv_id = res["artifact_ids"][0]
    row = deps.repos.conn.execute(
        "SELECT content_type, content FROM content_versions WHERE id=?", (cv_id,)
    ).fetchone()
    assert row is not None
    assert row["content_type"] == "transcript"
    assert len(row["content"]) > 0


async def test_transcribe_without_provider_fails() -> None:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    deps = Deps(repos=Repos(c), ingest=ingest, asr=None, ai=None)
    payload: dict[str, Any] = {"local_uri": "file://a.mp4"}
    res = await dispatch(_env("TranscribeSource", payload), deps)
    assert res["ok"] is False
    assert "UNAVAILABLE" in (res.get("error") or "")
