"""RenderSource 端到端测试（W6）：TTS→渲染→落 content_versions(video_draft)。"""

from __future__ import annotations

import json
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
    VideoDraftMeta,
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


async def test_render_end_to_end() -> None:
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
    out = await dispatch(env.model_dump(), deps)
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


def test_truncate_meta_json_valid_and_bounded() -> None:
    # R3 非阻项（quality #2）：超长 VideoDraftMeta 必须落库为合法 JSON 且 <= 上限
    from worker.runtime.handlers.render_source import _truncate_meta_json

    meta = VideoDraftMeta(
        video_uri="file:///tmp/out.mp4",
        duration_seconds=12.5,
        template="X" * 30000,
        tts_engine="synthesize",
        resolution=(1920, 1080),
        fps=30,
        source_version_id="cv-1",
    )
    out = _truncate_meta_json(meta)
    # 必须是合法 JSON（可回读）
    parsed = json.loads(out)
    assert isinstance(parsed, dict)
    # 不超过上限
    assert len(out) <= 20000
    # 超长字段确实被裁剪
    assert len(parsed.get("template", "")) < 30000
