"""场景切分与关键帧（PRD-ANA-003 精确分析）。

精确分析在快速分析（纯转写文本）之上，叠加**场景时间线**：用 ffmpeg 的
``select='gt(scene,THRESH)'`` 场景检测滤镜找出镜头切点，据此把媒体切成
若干场景，每个场景取中点作为代表**关键帧**时间戳，供分析引用来源位置
（PRD-ANA-005）与脚本对齐。

分层（便于离线验证）：
- 纯函数 :func:`parse_ffmpeg_duration` / :func:`parse_showinfo_pts` /
  :func:`build_scenes_from_boundaries` —— 无副作用，单测完整覆盖。
- :class:`FFmpegSceneDetector` —— 仅 subprocess 调用一层是真实 ffmpeg
  依赖（opt-in、不在 CI 覆盖）；解析/切分逻辑全走上面的纯函数。

ffmpeg 缺失时 detector.available 为 False，由 resolve/handler 转
``UNAVAILABLE``，绝不伪造场景。
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

# 默认场景切点灵敏度（0~1，越大越迟钝）；opts["threshold"] 可覆盖。
_DEFAULT_THRESHOLD = 0.4

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_PTS_TIME_RE = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


class Scene(BaseModel):
    """一个场景片段（镜头）及其代表关键帧时间戳（秒）。"""

    index: int
    start: float
    end: float
    keyframe_sec: float


def parse_ffmpeg_duration(stderr: str) -> float:
    """从 ffmpeg stderr 解析总时长（秒）；无法解析返回 0.0。"""
    m = _DURATION_RE.search(stderr)
    if not m:
        return 0.0
    h, mm, ss = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mm * 60 + ss


def parse_showinfo_pts(stderr: str) -> list[float]:
    """从 showinfo 输出解析场景切点 ``pts_time`` 列表（升序去重）。"""
    seen: set[float] = set()
    out: list[float] = []
    for m in _PTS_TIME_RE.finditer(stderr):
        t = float(m.group(1))
        if t not in seen:
            seen.add(t)
            out.append(t)
    out.sort()
    return out


def build_scenes_from_boundaries(
    boundaries: list[float], duration: float
) -> list[Scene]:
    """由切点 + 总时长构造场景列表（每段关键帧取中点）。

    - 过滤越界 / 非正的切点，去重排序。
    - 切点把 ``[0, duration]`` 分成 N+1 段；无切点则整段为单场景。
    - ``duration <= 0`` 时退化为单场景 ``[0, 0]``（时长未知的兜底）。
    """
    if duration <= 0:
        return [Scene(index=0, start=0.0, end=0.0, keyframe_sec=0.0)]
    cuts = sorted({b for b in boundaries if 0.0 < b < duration})
    starts = [0.0, *cuts]
    ends = [*cuts, duration]
    return [
        Scene(index=i, start=s, end=e, keyframe_sec=round((s + e) / 2, 3))
        for i, (s, e) in enumerate(zip(starts, ends, strict=True))
    ]


@runtime_checkable
class SceneDetector(Protocol):
    """场景切分 Provider 协议。"""

    name: str
    available: bool

    async def detect(
        self, media_uri: str, opts: dict[str, Any] | None = None
    ) -> list[Scene]:
        """检测 ``media_uri`` 的场景切点，返回场景列表。"""
        ...


class FFmpegSceneDetector:
    """基于 ffmpeg 场景检测滤镜的切分器（opt-in 真实媒体路径）。"""

    name = "ffmpeg-scene"

    def __init__(
        self, bin_path: str | None = None, threshold: float = _DEFAULT_THRESHOLD
    ) -> None:
        resolved = shutil.which("ffmpeg") if bin_path is None else bin_path
        self.bin_path: str | None = resolved or None
        self.available = bool(self.bin_path)
        self.threshold = threshold

    async def detect(
        self, media_uri: str, opts: dict[str, Any] | None = None
    ) -> list[Scene]:
        opts = opts or {}
        threshold = float(opts.get("threshold") or self.threshold)
        path = media_uri[7:] if media_uri.startswith("file://") else media_uri
        return await asyncio.to_thread(self._detect_sync, path, threshold)

    def _detect_sync(self, path: str, threshold: float) -> list[Scene]:
        if not self.bin_path:
            # available=False 时不应被调到；防御性返回单场景（时长未知）
            return build_scenes_from_boundaries([], 0.0)
        # argv list（不拼 shell）；showinfo 把每个入选帧的 pts_time 打到 stderr
        argv = [
            self.bin_path, "-hide_banner", "-nostats",
            "-i", path,
            "-filter:v", f"select='gt(scene,{threshold})',showinfo",
            "-an", "-f", "null", "-",
        ]
        proc = subprocess.run(argv, capture_output=True, text=True)  # noqa: S603
        stderr = proc.stderr or ""
        duration = parse_ffmpeg_duration(stderr)
        boundaries = parse_showinfo_pts(stderr)
        return build_scenes_from_boundaries(boundaries, duration)
