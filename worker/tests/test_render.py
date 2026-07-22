"""RenderSource 端到端测试（W6）：TTS→渲染→落 content_versions(video_draft)。"""

from __future__ import annotations

import os
import sys
import threading
from typing import Any

from worker.runtime.bootstrap import MIGRATIONS_DIR
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import (
    CommandEnvelope,
    ContentProject,
    ContentVersion,
    Workspace,
)
from worker.runtime.providers.renderer.ffmpeg import FFmpegRenderer
from worker.runtime.providers.tts.local import LocalTTSProvider
from worker.runtime.render.ffmpeg_runner import FFmpegRunner

PY = sys.executable
FAKE = os.path.join(os.path.dirname(__file__), "fakes", "fake_ffmpeg.py")


def _env(command_type: str, payload: dict[str, Any], project_id: str) -> CommandEnvelope:
    return CommandEnvelope(
        commandId="cmd-1",
        commandType=command_type,
        actor={"type": "user", "id": "u1"},
        source="ui",
        workspaceId="ws-x",
        projectId=project_id,
        payload=payload,
        requestedAt="2026-07-22T00:00:00Z",
    )


def test_render_end_to_end() -> None:
    conn = in_memory()
    run_migrations(conn, MIGRATIONS_DIR)
    repos = Repos(conn)
    ws = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj_id = repos.projects.insert(
        ContentProject(workspace_id=ws, title="p")
    )
    cv_id = repos.content_versions.insert(
        ContentVersion(
            project_id=prj_id,
            content_type="transcript",
            content="hello world this is a caption",
            content_hash="abc",
            producer={},
        )
    )
    deps = Deps(
        repos=repos,
        tts=LocalTTSProvider(),
        renderer=FFmpegRenderer(FFmpegRunner(bin_path=PY), ffmpeg_bin=FAKE),
    )
    env = _env(
        "CreateRenderJob",
        {"source_version_id": cv_id, "tts_engine": "synthesize"},
        prj_id,
    )
    out = dispatch(env.model_dump(), deps)
    assert out["ok"] is True, out
    rows = conn.execute(
        "SELECT id FROM content_versions WHERE content_type='video_draft'"
    ).fetchall()
    assert len(rows) == 1


def test_cancel_registry() -> None:
    from worker.runtime.jobs.cancel import clear, register, request

    ev = threading.Event()
    register("job-z", ev)
    assert request("job-z") is True
    assert ev.is_set()
    assert request("job-nope") is False
    clear("job-z")
