"""场景切分纯函数与 detector 可用性测试（PRD-ANA-003）。

真实 ffmpeg 子进程路径（``FFmpegSceneDetector._detect_sync`` 的 subprocess
调用）不在 CI 覆盖内（无测试媒体，opt-in）；此处完整覆盖解析/切分逻辑。
"""

from __future__ import annotations

from worker.runtime.analysis.scene import (
    FFmpegSceneDetector,
    build_scenes_from_boundaries,
    parse_ffmpeg_duration,
    parse_showinfo_pts,
)

_SHOWINFO_STDERR = """
  Duration: 00:00:12.50, start: 0.000000, bitrate: 800 kb/s
[Parsed_showinfo_1 @ 0x1] n:0 pts:120000 pts_time:5.0 pos:1 fmt:yuv420p
[Parsed_showinfo_1 @ 0x1] n:1 pts:240000 pts_time:9.2 pos:2 fmt:yuv420p
"""


def test_parse_ffmpeg_duration() -> None:
    assert parse_ffmpeg_duration(_SHOWINFO_STDERR) == 12.5
    assert parse_ffmpeg_duration("no duration here") == 0.0


def test_parse_showinfo_pts_sorted_unique() -> None:
    pts = parse_showinfo_pts(_SHOWINFO_STDERR)
    assert pts == [5.0, 9.2]
    # 去重 + 升序
    dup = "pts_time:3.0 x pts_time:1.0 y pts_time:3.0"
    assert parse_showinfo_pts(dup) == [1.0, 3.0]


def test_build_scenes_from_boundaries_splits() -> None:
    scenes = build_scenes_from_boundaries([5.0, 9.2], 12.5)
    assert len(scenes) == 3
    assert (scenes[0].start, scenes[0].end) == (0.0, 5.0)
    assert (scenes[1].start, scenes[1].end) == (5.0, 9.2)
    assert (scenes[2].start, scenes[2].end) == (9.2, 12.5)
    # 关键帧取中点
    assert scenes[0].keyframe_sec == 2.5
    assert scenes[2].keyframe_sec == round((9.2 + 12.5) / 2, 3)
    # index 连续
    assert [s.index for s in scenes] == [0, 1, 2]


def test_build_scenes_no_boundaries_single_scene() -> None:
    scenes = build_scenes_from_boundaries([], 8.0)
    assert len(scenes) == 1
    assert (scenes[0].start, scenes[0].end) == (0.0, 8.0)


def test_build_scenes_filters_out_of_range_cuts() -> None:
    # 越界（<=0 或 >=duration）切点被丢弃
    scenes = build_scenes_from_boundaries([-1.0, 0.0, 6.0, 10.0, 99.0], 10.0)
    assert [(s.start, s.end) for s in scenes] == [(0.0, 6.0), (6.0, 10.0)]


def test_build_scenes_unknown_duration_degrades() -> None:
    scenes = build_scenes_from_boundaries([3.0], 0.0)
    assert len(scenes) == 1
    assert scenes[0].end == 0.0


def test_detector_unavailable_when_ffmpeg_missing() -> None:
    # 显式给不存在的 bin_path：available 为 False
    det = FFmpegSceneDetector(bin_path="")
    assert det.available is False
    assert det.name == "ffmpeg-scene"
