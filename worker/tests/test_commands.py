"""Batch 0：Command Bus（envelope 校验 + ImportSource 路由）测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.commands.envelope import EnvelopeError, parse_envelope
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _deps() -> Deps:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    return Deps(repos=Repos(c), ingest=ingest, asr=None, ai=None)


def _envelope(command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "commandId": "cmd-1",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": "ws-1",
        "payload": payload,
        "requestedAt": "2026-07-21T00:00:00+00:00",
    }


def test_parse_envelope_invalid() -> None:
    raised = False
    try:
        parse_envelope({"foo": "bar"})
    except EnvelopeError:
        raised = True
    assert raised, "expected EnvelopeError"


def test_dispatch_import_source() -> None:
    deps = _deps()
    payload: dict[str, Any] = {
        "local_uri": "file://a.mp4",
        "content_hash": "h123",
        "kind": "video",
    }
    res = dispatch(_envelope("ImportSource", payload), deps)
    assert res["ok"] is True
    assert len(res["artifact_ids"]) == 1


def test_dispatch_unknown() -> None:
    deps = _deps()
    res = dispatch(_envelope("NoSuchCommand", {}), deps)
    assert res["ok"] is False
