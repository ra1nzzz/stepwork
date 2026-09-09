"""RenderSource 端到端测试（W6）：TTS→渲染→落 content_versions(video_draft)。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from typing import Any

import pytest

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


async def test_render_reports_tts_invocation_cost() -> None:
    """PRD-REN-002：旁白合成的来源与预计费用必须可见（此前恒为 None）。"""
    conn = in_memory()
    run_migrations(conn, MIGRATIONS_DIR)
    repos = Repos(conn)
    ws = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj_id = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    text = "旁白文本" * 50  # 200 字符
    cv_id = repos.content_versions.insert(
        ContentVersion(
            project_id=prj_id,
            content_type="transcript",
            content=text,
            content_hash="abc",
            producer={},
        )
    )

    class _PricedTTS(LocalTTSProvider):
        name = "priced-tts"
        model = "tts-1"
        estimated_cost_per_1k = 2.0  # 每千字符 2.0

    deps = Deps(
        repos=repos,
        tts=_PricedTTS(),
        renderer=FFmpegRenderer(FFmpegRunner(bin_path=PY), ffmpeg_bin=FAKE),
    )
    env = _env(
        "CreateRenderJob",
        {"source_version_id": cv_id, "tts_engine": "synthesize"},
        prj_id,
    )
    out = await dispatch(env.model_dump(), deps)
    assert out["ok"] is True, out

    inv = out["detail"]["tts_invocation"]
    assert inv is not None, "TTS 调用必须出现在 detail 中"
    assert inv["provider"] == "priced-tts"
    # 按实际字符量计价：200 字符 × 2.0/1k = 0.4（此前 chars 恒为 0）
    assert inv["estimated_cost"] == 0.4

    # 审计行：renderer + tts 各一条
    rows = conn.execute(
        "SELECT payload FROM audit_events WHERE event_type='provider_invocation'"
    ).fetchall()
    providers = {json.loads(r["payload"])["provider"] for r in rows}
    assert "priced-tts" in providers


def _seed_script_version(conn: Any) -> str:
    """建 ws/project/script ContentVersion，返回 version id（供 FK 用）。"""
    repos = Repos(conn)
    ws = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    return repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            content_type="script",
            content="第一幕文本",
            content_hash="h",
            producer={},
        )
    )


def test_video_scenes_roundtrip_and_unique_seq() -> None:
    """S2 地基：分幕事实表可写可读，且同版本 seq 唯一（并发写不互相覆盖）。"""
    conn = in_memory()
    run_migrations(conn, MIGRATIONS_DIR)
    version_id = _seed_script_version(conn)
    conn.execute(
        "INSERT INTO video_scenes "
        "(id, version_id, seq, text, emotion, highlight, duration_sec, born_at_sec,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("vs_1", version_id, 0, "第一幕文本", "calm", "第一幕", 6.946, 0.0, "2026-09-09"),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM video_scenes WHERE id='vs_1'").fetchone()
    assert row["seq"] == 0
    assert row["duration_sec"] == 6.946
    assert row["audio_uri"] is None  # 未配音
    assert row["image_uri"] is None  # A 版零素材依赖
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO video_scenes (id, version_id, seq, text, created_at) "
            "VALUES (?,?,?,?,?)",
            ("vs_2", version_id, 0, "重复序号", "2026-09-09"),
        )


async def test_render_source_forwards_per_request_renderer(monkeypatch: Any) -> None:
    """P4：payload.renderer 必须能覆盖 deps.renderer（否则 GUI 无法按项目切换）。"""
    import worker.runtime.handlers.render_source as rs

    conn = in_memory()
    run_migrations(conn, MIGRATIONS_DIR)
    repos = Repos(conn)
    ws = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj_id = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    cv_id = repos.content_versions.insert(
        ContentVersion(
            project_id=prj_id,
            content_type="transcript",
            content="hello world",
            content_hash="abc",
            producer={},
        )
    )
    hinted = FFmpegRenderer(FFmpegRunner(bin_path=PY), ffmpeg_bin=FAKE)
    seen: list[Any] = []

    def _recorder(hint: Any, runner: Any = None) -> Any:
        seen.append(hint)
        return hinted

    monkeypatch.setattr(rs, "renderer_from_hint", _recorder)
    deps = Deps(
        repos=repos,
        tts=LocalTTSProvider(),
        # 默认渲染器与 hint 返回的不同 → 能区分到底用了谁
        renderer=FFmpegRenderer(FFmpegRunner(bin_path=PY), ffmpeg_bin=FAKE),
    )
    env = _env(
        "CreateRenderJob",
        {"source_version_id": cv_id, "tts_engine": "synthesize", "renderer": "ffmpeg"},
        prj_id,
    )
    out = await dispatch(env.model_dump(), deps)
    assert out["ok"] is True, out
    assert seen == ["ffmpeg"], "handler 必须把 payload.renderer 透传给解析器"


async def test_render_user_audio_has_no_tts_invocation() -> None:
    """用户录音路径不调 TTS，不应产生 TTS 费用记录（PRD-REN-003）。"""
    conn = in_memory()
    run_migrations(conn, MIGRATIONS_DIR)
    repos = Repos(conn)
    ws = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj_id = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    cv_id = repos.content_versions.insert(
        ContentVersion(
            project_id=prj_id,
            content_type="transcript",
            content="用户自带录音",
            content_hash="abc2",
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
        {
            "source_version_id": cv_id,
            "tts_engine": "user_audio",
            "user_audio_uri": "file:///tmp/my.wav",
        },
        prj_id,
    )
    out = await dispatch(env.model_dump(), deps)
    assert out["ok"] is True, out
    assert out["detail"]["tts_invocation"] is None
