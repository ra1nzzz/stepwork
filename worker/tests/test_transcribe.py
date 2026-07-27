"""Batch 1：TranscribeSource handler（经 Command Bus 端到端）测试。"""

from __future__ import annotations

import json
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


async def test_transcript_carries_timestamps() -> None:
    """PRD-ANA-001 的验收是「输出**文本和时间戳**」。

    此前只断言 ``len(content) > 0`` —— 那连是不是转写结果都证明不了，
    更别说时间戳。时间戳是下游一切的地基：字幕、精确分析的引用锚点、
    剪辑时间线的 marker 全靠它，丢了不会报错，只会静默退化。
    """
    deps = _deps()
    res = await dispatch(
        _env("TranscribeSource", {"local_uri": "file://a.mp4", "opts": {"duration_sec": 12}}),
        deps,
    )
    assert res["ok"] is True

    row = deps.repos.conn.execute(
        "SELECT content, producer FROM content_versions WHERE id=?",
        (res["artifact_ids"][0],),
    ).fetchone()
    # 正文是纯文本，时间戳在 producer.segments（见 transcribe_source）
    assert row["content"].strip(), "转写正文不能为空"
    producer = json.loads(row["producer"])
    segments = producer["segments"]
    assert segments, "必须有分段，否则没有任何时间戳"
    for seg in segments:
        assert isinstance(seg["start"], int | float)
        assert isinstance(seg["end"], int | float)
        assert seg["end"] >= seg["start"], f"结束时间早于开始时间：{seg}"
        assert seg["text"].strip()
    # 分段必须按时间递增，否则字幕与时间线会乱序
    starts = [s["start"] for s in segments]
    assert starts == sorted(starts), starts
