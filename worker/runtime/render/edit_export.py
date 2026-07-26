"""导出第三方剪辑数据（PRD-REN-006）。

**为什么不是剪映**（调研结论，2026-07）：

- 剪映从 6.0 起对 ``draft_content.json`` 加密，之后连 ``draft_meta_info.json``
  也加了；当前主线版本已到 10.x。要写剪映草稿只剩两条路：
  (a) 只支持 5.9 及以下 —— 早已过时；
  (b) 内置社区逆向出来的解密/回加密算法 —— 那是**绕过第三方软件的技术
      保护措施**，与本项目「不包含反检测、规避风控代码」的既定立场冲突，
      且加密方案随版本变化，今天能跑明天就废。
  两条都不该做，故不实现剪映草稿导出。
- OpenCut（开源在线剪辑）目前**也没有**可移植的工程文件格式：项目锁在
  IndexedDB/OPFS 里，可导出成品视频但不能导出时间线；社区 Issue #719 正
  在请求 JSON 工程格式，尚未实现。等它落地后可再补一个 exporter。

**所以选 OTIO**（OpenTimelineIO，Academy Software Foundation 的开放标准）：
纯 JSON、有版本化 schema、DaVinci Resolve / Premiere / Final Cut / Avid
都能读。另附 CMX3600 EDL 作为最大公约数（几乎所有 NLE 都认，代价是只有
剪辑点、没有元数据）。两者都用标准库生成，不引入新依赖。

导出的时间线用的是**别的工具没有的数据**：精确分析检出的场景切点、
逐字稿的时间戳。用户可以直接在专业 NLE 里接着剪，而不是从零拉时间线。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: 导出格式
FORMAT_OTIO = "otio"
FORMAT_EDL = "edl"
SUPPORTED_FORMATS = (FORMAT_OTIO, FORMAT_EDL)

#: 默认帧率。素材未提供时用 25（PAL），只影响时间码换算的呈现，
#: 不影响秒级的真实切点。
DEFAULT_FPS = 25.0


def _rational(seconds: float, fps: float) -> dict[str, Any]:
    """秒 → OTIO RationalTime（以帧为单位）。"""
    return {
        "OTIO_SCHEMA": "RationalTime.1",
        "rate": fps,
        "value": round(seconds * fps, 3),
    }


def _time_range(start: float, duration: float, fps: float) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": _rational(start, fps),
        "duration": _rational(duration, fps),
    }


def _to_file_url(path: str) -> str:
    """本地路径 → file URL。

    Windows 反斜杠必须转成正斜杠，否则 NLE 解析 target_url 时找不到素材。
    """
    if not path:
        return ""
    if path.startswith(("file://", "http://", "https://")):
        return path
    return Path(path).absolute().as_uri()


def _clip(
    name: str, media_url: str, start: float, duration: float, fps: float
) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "Clip.1",
        "name": name,
        "effects": [],
        "markers": [],
        "metadata": {},
        "media_reference": {
            "OTIO_SCHEMA": "ExternalReference.1",
            "target_url": media_url,
        },
        "source_range": _time_range(start, duration, fps),
    }


def _marker(name: str, start: float, duration: float, fps: float) -> dict[str, Any]:
    """逐字稿片段 → OTIO marker，在 NLE 时间线上直接看得到台词位置。"""
    return {
        "OTIO_SCHEMA": "Marker.2",
        "name": name,
        "color": "GREEN",
        "metadata": {},
        "marked_range": _time_range(start, duration, fps),
    }


def build_otio(
    *,
    name: str,
    media_path: str,
    scenes: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    fps: float = DEFAULT_FPS,
) -> dict[str, Any]:
    """构造 OTIO Timeline。

    Args:
        name: 时间线名称（通常是项目标题）。
        media_path: 源素材本地路径。
        scenes: ``[{index,start,end}]``；空列表则整段一个 clip。
        segments: 逐字稿 ``[{start,end,text}]``，转成 marker。
        fps: 帧率。
    """
    media_url = _to_file_url(media_path)
    clips: list[dict[str, Any]] = []
    if scenes:
        for scene in scenes:
            start = float(scene.get("start") or 0.0)
            end = float(scene.get("end") or 0.0)
            if end <= start:
                continue
            clips.append(
                _clip(
                    f"scene-{scene.get('index', len(clips))}",
                    media_url,
                    start,
                    end - start,
                    fps,
                )
            )
    if not clips:
        # 没有场景信息（未跑精确分析）时整段一个 clip，仍然可用
        clips.append(_clip(name or "clip", media_url, 0.0, 0.0, fps))

    markers = [
        _marker(
            str(seg.get("text") or "")[:60],
            float(seg.get("start") or 0.0),
            max(float(seg.get("end") or 0.0) - float(seg.get("start") or 0.0), 0.0),
            fps,
        )
        for seg in segments
        if str(seg.get("text") or "").strip()
    ]

    video_track: dict[str, Any] = {
        "OTIO_SCHEMA": "Track.1",
        "name": "V1",
        "kind": "Video",
        "children": clips,
        "effects": [],
        # 台词标记挂在轨道上（而不是逐个 clip），跨场景的句子才不会被切碎
        "markers": markers,
        "metadata": {},
        "source_range": None,
    }
    return {
        "OTIO_SCHEMA": "Timeline.1",
        "name": name or "STEPWORK timeline",
        "metadata": {
            "stepwork": {
                "generator": "STEPWORK",
                # 注明场景来自精确分析，便于回溯这条时间线是怎么来的
                "scene_source": "precise-analysis" if scenes else "none",
                "marker_count": len(markers),
            }
        },
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "name": "tracks",
            "children": [video_track],
            "effects": [],
            "markers": [],
            "metadata": {},
            "source_range": None,
        },
    }


def _timecode(seconds: float, fps: float) -> str:
    """秒 → 非丢帧时间码 HH:MM:SS:FF。"""
    total_frames = int(round(max(seconds, 0.0) * fps))
    frames = int(total_frames % round(fps))
    total_seconds = total_frames // int(round(fps))
    return (
        f"{total_seconds // 3600:02d}:"
        f"{(total_seconds % 3600) // 60:02d}:"
        f"{total_seconds % 60:02d}:{frames:02d}"
    )


def build_edl(
    *,
    name: str,
    media_path: str,
    scenes: list[dict[str, Any]],
    fps: float = DEFAULT_FPS,
) -> str:
    """构造 CMX3600 EDL。

    EDL 只有剪辑点、没有元数据，胜在**几乎所有 NLE 都认**。作为 OTIO 之外
    的兜底：万一对方软件读不了 OTIO，至少切点能带过去。
    """
    reel = (Path(media_path).stem[:8] or "AX").upper()
    lines = [f"TITLE: {name or 'STEPWORK'}", "FCM: NON-DROP FRAME", ""]
    record = 0.0
    ranges = (
        [(float(s.get("start") or 0.0), float(s.get("end") or 0.0)) for s in scenes]
        if scenes
        else [(0.0, 0.0)]
    )
    event = 0
    for start, end in ranges:
        if end <= start and scenes:
            continue
        event += 1
        duration = max(end - start, 0.0)
        lines.append(
            f"{event:03d}  {reel:<8} V     C        "
            f"{_timecode(start, fps)} {_timecode(end, fps)} "
            f"{_timecode(record, fps)} {_timecode(record + duration, fps)}"
        )
        lines.append(f"* FROM CLIP NAME: {Path(media_path).name}")
        record += duration
    return "\n".join(lines) + "\n"


def write_timeline(
    out_dir: Path,
    *,
    fmt: str,
    name: str,
    media_path: str,
    scenes: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    fps: float = DEFAULT_FPS,
) -> Path:
    """落盘并返回文件路径。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in (name or "timeline") if c.isalnum() or c in "-_") or "timeline"
    if fmt == FORMAT_OTIO:
        # 官方约定用 .otio 扩展名而不是 .json
        target = out_dir / f"{safe}.otio"
        target.write_text(
            json.dumps(
                build_otio(
                    name=name,
                    media_path=media_path,
                    scenes=scenes,
                    segments=segments,
                    fps=fps,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return target
    if fmt == FORMAT_EDL:
        target = out_dir / f"{safe}.edl"
        target.write_text(
            build_edl(name=name, media_path=media_path, scenes=scenes, fps=fps),
            encoding="utf-8",
        )
        return target
    raise ValueError(f"unsupported format {fmt!r}; expected one of {SUPPORTED_FORMATS}")
