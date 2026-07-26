"""Tranche 2：渲染产物（PRD-REN-001/003）测试。

覆盖：

1. ``build_srt``：合法 SRT 结构 / 时间戳单调 / 总时长等比分配 / 空文本兜底。
2. 渲染端到端（fake ffmpeg）：detail.artifacts {video, subtitles, audio}、
   .srt sidecar 与 mp4 同目录、TTS 音频保留（不再删除）、
   VideoDraftMeta 登记 audio_uri / subtitles_uri。
"""

from __future__ import annotations

import json
import os
import re
import sys
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
from worker.runtime.render.subtitles import build_srt, split_sentences

PY = sys.executable
FAKE = os.path.join(os.path.dirname(__file__), "fakes", "fake_ffmpeg.py")

_TS_RE = re.compile(
    r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$"
)


def _is_file(path: str) -> bool:
    """同步 helper：检查文件存在性（避免 async 测试函数内触发 ASYNC240）。"""
    return os.path.isfile(path)


def _read_text(path: str) -> str:
    """同步 helper：读文本文件（避免 async 测试函数内触发 ASYNC230）。"""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _parse_srt(srt: str) -> list[tuple[int, str, str]]:
    """解析 SRT 为 (序号, 时间行, 文本) 三元组列表（格式校验）。"""
    entries: list[tuple[int, str, str]] = []
    for block in [b for b in srt.strip().split("\n\n") if b.strip()]:
        lines = block.split("\n")
        assert len(lines) >= 3, f"malformed SRT block: {block!r}"
        idx = int(lines[0])
        assert _TS_RE.match(lines[1]), f"bad timestamp line: {lines[1]!r}"
        entries.append((idx, lines[1], "\n".join(lines[2:])))
    return entries


def test_split_sentences_cn_en() -> None:
    assert split_sentences("第一句。第二句！Third?") == [
        "第一句。", "第二句！", "Third?"
    ]
    assert split_sentences("") == ["..."]


def test_build_srt_legal_and_proportional() -> None:
    srt = build_srt("短句。这是一个非常非常长的句子哦。", 10.0)
    entries = _parse_srt(srt)
    assert [e[0] for e in entries] == [1, 2]
    # 末条终点 == 音频总时长
    assert entries[-1][1].endswith("00:00:10,000")
    # 时间戳单调不重叠
    prev_end = "00:00:00,000"
    for _idx, ts_line, _text in entries:
        start, end = ts_line.split(" --> ")
        assert start >= prev_end
        assert end > start
        prev_end = end
    # 长句占比更大
    first_end = entries[0][1].split(" --> ")[1]
    assert first_end < "00:00:05,000"


def test_build_srt_zero_duration_fallback() -> None:
    srt = build_srt("一句。二句。", 0.0)
    entries = _parse_srt(srt)
    assert len(entries) == 2
    assert entries[-1][1].split(" --> ")[1] > "00:00:00,000"


def _env(command_type: str, payload: dict[str, Any], project_id: str) -> CommandEnvelope:
    return CommandEnvelope(
        commandId="cmd-1",
        commandType=command_type,
        actor={"type": "user", "id": "u1"},
        source="ui",
        workspaceId="ws-x",
        projectId=project_id,
        payload=payload,
        requestedAt="2026-07-26T00:00:00Z",
    )


async def test_render_artifacts_srt_and_audio_kept() -> None:
    conn = in_memory()
    run_migrations(conn, MIGRATIONS_DIR)
    repos = Repos(conn)
    ws = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj_id = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    cv_id = repos.content_versions.insert(
        ContentVersion(
            project_id=prj_id,
            content_type="transcript",
            content="第一句话。第二句话。",
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

    artifacts = out["detail"]["artifacts"]
    # 三产物齐备且为绝对路径
    assert os.path.isabs(artifacts["video"])
    assert artifacts["subtitles"] and _is_file(artifacts["subtitles"])

    # .srt 与 mp4 同目录、合法 SRT、含脚本文本
    assert os.path.dirname(artifacts["subtitles"]) == os.path.dirname(
        artifacts["video"]
    )
    srt_text = _read_text(artifacts["subtitles"])
    entries = _parse_srt(srt_text)
    assert len(entries) == 2
    assert "第一句话。" in srt_text

    # TTS 音频保留（Tranche 2：不再在 finally 删除）
    assert artifacts["audio"] and _is_file(artifacts["audio"])

    # VideoDraftMeta 登记 subtitles_uri / audio_uri
    row = conn.execute(
        "SELECT content FROM content_versions WHERE content_type='video_draft'"
    ).fetchone()
    meta = json.loads(row["content"])
    assert meta["subtitles_uri"] == artifacts["subtitles"]
    assert meta["audio_uri"].endswith(os.path.basename(artifacts["audio"]))

    # 费用透明：渲染也带 invocation（renderer 无成本配置 → None）
    invocation = out["detail"]["invocation"]
    assert invocation["provider"] == "ffmpeg-renderer"
    assert invocation["estimated_cost"] is None
