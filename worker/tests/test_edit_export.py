"""剪辑时间线导出测试（PRD-REN-006）。

格式选择的调研结论见 ``render/edit_export.py``：剪映 6+ 起草稿加密（现已
10.x），OpenCut 尚无可移植工程格式，故导 OTIO + EDL。

重点验证「导出的东西真能被 NLE 用」：schema 标识正确、素材路径是可解析的
file URL（Windows 反斜杠尤其容易翻车）、场景切点如实落到 clip 上。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentVersion, SourceAsset
from worker.runtime.render.edit_export import (
    build_edl,
    build_otio,
    write_timeline,
)

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"

_SCENES = [
    {"index": 0, "start": 0.0, "end": 3.0, "keyframe_sec": 1.5},
    {"index": 1, "start": 3.0, "end": 7.5, "keyframe_sec": 5.25},
]
_SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "开场白"},
    {"start": 2.0, "end": 5.0, "text": "正文内容"},
    {"start": 5.0, "end": 7.0, "text": "   "},  # 空白段应被跳过
]


def _env(command_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "desktop", "id": "ui"},
        "source": "ui",
        "workspaceId": "ws-local",
        "requestedAt": datetime.now(UTC).isoformat(),
        "payload": payload or {},
    }


def _new_db(tmp_path: Path) -> tuple[sqlite3.Connection, Repos]:
    conn = connect(str(tmp_path / "exp.db"))
    run_migrations(conn, _MIG_DIR)
    return conn, Repos(conn)


def _run(raw: dict[str, Any], deps: Deps) -> dict[str, Any]:
    return asyncio.run(dispatch(raw, deps))


# --------------------------------------------------------------------------
# OTIO
# --------------------------------------------------------------------------


def test_otio_has_required_schema_identifiers() -> None:
    """OTIO 靠每个对象上的 OTIO_SCHEMA 识别类型，缺了就整个读不了。"""
    tl = build_otio(
        name="项目", media_path="/media/a.mp4", scenes=_SCENES, segments=_SEGMENTS
    )
    assert tl["OTIO_SCHEMA"] == "Timeline.1"
    assert tl["tracks"]["OTIO_SCHEMA"] == "Stack.1"
    track = tl["tracks"]["children"][0]
    assert track["OTIO_SCHEMA"] == "Track.1"
    assert track["kind"] == "Video"
    for clip in track["children"]:
        assert clip["OTIO_SCHEMA"] == "Clip.1"
        assert clip["media_reference"]["OTIO_SCHEMA"] == "ExternalReference.1"
        assert clip["source_range"]["OTIO_SCHEMA"] == "TimeRange.1"
        assert clip["source_range"]["start_time"]["OTIO_SCHEMA"] == "RationalTime.1"


def test_otio_clips_match_scene_cuts() -> None:
    """场景切点必须如实落到 clip 上，否则导出的时间线是假的。"""
    tl = build_otio(
        name="p", media_path="/media/a.mp4", scenes=_SCENES, segments=[], fps=25.0
    )
    clips = tl["tracks"]["children"][0]["children"]
    assert len(clips) == 2
    # 第二段 3.0s~7.5s → start 75 帧、时长 112.5 帧
    assert clips[1]["source_range"]["start_time"]["value"] == pytest.approx(75.0)
    assert clips[1]["source_range"]["duration"]["value"] == pytest.approx(112.5)


def test_otio_without_scenes_still_produces_one_clip() -> None:
    """没跑精确分析也要能导出（整段单片段），不能给个空时间线。"""
    tl = build_otio(name="p", media_path="/media/a.mp4", scenes=[], segments=[])
    clips = tl["tracks"]["children"][0]["children"]
    assert len(clips) == 1
    assert tl["metadata"]["stepwork"]["scene_source"] == "none"


def test_otio_skips_degenerate_scenes() -> None:
    """end<=start 的坏场景不能变成零长 clip（NLE 会当损坏处理）。"""
    tl = build_otio(
        name="p",
        media_path="/media/a.mp4",
        scenes=[{"index": 0, "start": 5.0, "end": 5.0}, *_SCENES],
        segments=[],
    )
    clips = tl["tracks"]["children"][0]["children"]
    assert len(clips) == 2


def test_otio_markers_come_from_transcript() -> None:
    tl = build_otio(
        name="p", media_path="/media/a.mp4", scenes=_SCENES, segments=_SEGMENTS
    )
    markers = tl["tracks"]["children"][0]["markers"]
    # 空白段被跳过
    assert [m["name"] for m in markers] == ["开场白", "正文内容"]
    assert markers[0]["OTIO_SCHEMA"] == "Marker.2"


def test_media_url_is_a_resolvable_file_url() -> None:
    """target_url 必须能被解析回真实路径。

    Windows 路径里的反斜杠若不转成正斜杠，NLE 打开时会找不到素材 ——
    这是跨平台导出最容易翻车的一处。
    """
    tl = build_otio(name="p", media_path=str(Path("D:/媒体/素材 1.mp4")), scenes=[], segments=[])
    url = tl["tracks"]["children"][0]["children"][0]["media_reference"]["target_url"]
    assert url.startswith("file://")
    assert "\\" not in url
    # 反解回来应指向同一个文件名（含空格与中文）
    resolved = Path(url2pathname(urlparse(url).path))
    assert resolved.name == "素材 1.mp4"


def test_existing_url_is_not_double_encoded() -> None:
    tl = build_otio(
        name="p", media_path="file:///already/encoded.mp4", scenes=[], segments=[]
    )
    url = tl["tracks"]["children"][0]["children"][0]["media_reference"]["target_url"]
    assert url == "file:///already/encoded.mp4"


# --------------------------------------------------------------------------
# EDL
# --------------------------------------------------------------------------


def test_edl_timecodes_are_well_formed() -> None:
    edl = build_edl(name="p", media_path="/media/a.mp4", scenes=_SCENES, fps=25.0)
    lines = [ln for ln in edl.splitlines() if ln and ln[0].isdigit()]
    assert len(lines) == 2
    assert "NON-DROP FRAME" in edl
    # 3.0s @25fps = 00:00:03:00；7.5s = 00:00:07:12（12.5 帧四舍五入到 12/13）
    assert "00:00:00:00 00:00:03:00" in lines[0]
    assert lines[1].split()[4] == "00:00:03:00"


def test_edl_record_time_is_continuous() -> None:
    """录制时间码必须连续累加，否则导入后片段会叠在一起。"""
    edl = build_edl(name="p", media_path="/media/a.mp4", scenes=_SCENES, fps=25.0)
    lines = [ln for ln in edl.splitlines() if ln and ln[0].isdigit()]
    # 第一段录制 0~3s，第二段应从 3s 接着排
    assert lines[0].split()[6] == "00:00:00:00"
    assert lines[1].split()[6] == "00:00:03:00"


def test_write_timeline_rejects_unknown_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported format"):
        write_timeline(
            tmp_path, fmt="fcpxml", name="p", media_path="/a.mp4", scenes=[], segments=[]
        )


def test_written_otio_uses_dot_otio_extension(tmp_path: Path) -> None:
    """官方约定用 .otio 而不是 .json —— 有些 NLE 按扩展名识别。"""
    target = write_timeline(
        tmp_path, fmt="otio", name="我的项目", media_path="/a.mp4", scenes=[], segments=[]
    )
    assert target.suffix == ".otio"
    assert json.loads(target.read_text(encoding="utf-8"))["OTIO_SCHEMA"] == "Timeline.1"


# --------------------------------------------------------------------------
# 命令
# --------------------------------------------------------------------------


def _seed(deps: Deps, tmp_path: Path, *, with_scenes: bool) -> str:
    pid = _run(_env("CreateProject", {"title": "剪辑导出"}), deps)["detail"]["project"]["id"]
    media = tmp_path / "src.mp4"
    media.write_bytes(b"\x00")
    deps.repos.source_assets.insert_dedup(
        SourceAsset(
            project_id=pid,
            kind="video",
            local_uri=str(media),
            content_hash="h1",
            metadata={"fps": 30.0},
        )
    )
    # 逐字稿：**正文是纯文本，分段在 producer 里**（与 transcribe_source
    # 的真实落库形态一致）。此前这里按自己的假设写成 JSON content，于是
    # 测试通过而线上永远取不到 marker —— 测的是假设，不是系统。
    deps.repos.content_versions.insert(
        ContentVersion(
            project_id=pid,
            content_type="transcript",
            content="开场白\n正文内容",
            content_hash="h2",
            producer={"kind": "asr", "provider": "local", "segments": _SEGMENTS},
        )
    )
    deps.repos.content_versions.insert(
        ContentVersion(
            project_id=pid,
            content_type="analysis",
            content="{}",
            content_hash="h3",
            producer={"kind": "ai-analysis", "mode": "precise",
                      "scenes": _SCENES if with_scenes else []},
        )
    )
    return str(pid)


def test_export_command_writes_otio(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        pid = _seed(deps, tmp_path, with_scenes=True)
        res = _run(_env("ExportEditTimeline", {"projectId": pid, "format": "otio"}), deps)
        assert res["ok"] is True, res
        assert res["detail"]["scene_count"] == 2
        assert res["detail"]["marker_count"] == 3
        target = Path(res["detail"]["path"])
        assert target.is_file()
        data = json.loads(target.read_text(encoding="utf-8"))
        assert len(data["tracks"]["children"][0]["children"]) == 2
        # 素材 fps 应被采用（30 而非默认 25）
        rate = data["tracks"]["children"][0]["children"][0]["source_range"]["start_time"]["rate"]
        assert rate == 30.0
    finally:
        conn.close()


def test_export_without_precise_analysis_says_so(tmp_path: Path) -> None:
    """没跑精确分析时要如实说明是整段单片段，而不是假装切好了。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        pid = _seed(deps, tmp_path, with_scenes=False)
        res = _run(_env("ExportEditTimeline", {"projectId": pid}), deps)
        assert res["ok"] is True, res
        assert res["detail"]["scene_count"] == 0
        assert "未跑过精确分析" in res["detail"]["note"]
    finally:
        conn.close()


def test_export_edl_format(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        pid = _seed(deps, tmp_path, with_scenes=True)
        res = _run(_env("ExportEditTimeline", {"projectId": pid, "format": "edl"}), deps)
        assert res["ok"] is True, res
        assert Path(res["detail"]["path"]).suffix == ".edl"
    finally:
        conn.close()


def test_export_rejects_unknown_format(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        pid = _seed(deps, tmp_path, with_scenes=True)
        res = _run(
            _env("ExportEditTimeline", {"projectId": pid, "format": "jianying"}), deps
        )
        assert res["ok"] is False
        assert str(res["error"]).startswith("INVALID_ARGUMENT")
    finally:
        conn.close()


def test_export_without_assets_is_rejected(tmp_path: Path) -> None:
    """项目里没素材时时间线指不到文件，如实拒绝而不是导个空壳。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        pid = _run(_env("CreateProject", {"title": "空"}), deps)["detail"]["project"]["id"]
        res = _run(_env("ExportEditTimeline", {"projectId": pid}), deps)
        assert res["ok"] is False
        assert "没有素材" in str(res["error"])
    finally:
        conn.close()
