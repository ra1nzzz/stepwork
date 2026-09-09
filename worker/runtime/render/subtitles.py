"""SRT 字幕 sidecar 生成（Tranche 2，PRD-REN-003）。

两条路径，**优先用实测时间轴**：

- :func:`build_srt_from_scenes`：按 ``video_scenes`` 的实测 ``start_sec`` /
  ``duration_sec`` 生成 —— 配音完成后这才是准的（S2 验收「字幕与配音对齐」）；
- :func:`build_srt`：没有分幕时间轴时的**退路**，按字符量等比分配总时长。
  真实语速并不均匀（数字、停顿、专有名词），等比分配必然与配音错位，
  所以它只能是退路，不能是默认。

输出均为合法 SRT（序号 / ``HH:MM:SS,mmm --> HH:MM:SS,mmm`` / 文本 / 空行）。
"""

from __future__ import annotations

import re
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

# 句子切分：中英文句末标点 + 换行
_SENTENCE_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")

# 音频时长未知时的兜底：每句 2 秒
_FALLBACK_SECONDS_PER_LINE = 2.0


def split_sentences(text: str) -> list[str]:
    """把文本拆为句/行（去空白；空文本返回单占位行）。"""
    parts = [p.strip() for p in _SENTENCE_RE.findall(text or "")]
    parts = [p for p in parts if p]
    return parts or [(text or "").strip() or "..."]


def _fmt_ts(seconds: float) -> str:
    """秒 → ``HH:MM:SS,mmm``（SRT 时间戳格式）。"""
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    s = (total_ms // 1000) % 60
    m = (total_ms // 60000) % 60
    h = total_ms // 3600000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(text: str, total_duration_sec: float) -> str:
    """构造合法 SRT 字符串（时长按字符量等比分配）。

    Args:
        text: 脚本/字幕全文。
        total_duration_sec: 音频总时长；``<= 0`` 时按每句 2 秒兜底。

    Returns:
        SRT 全文（末尾含换行）。
    """
    lines = split_sentences(text)
    if total_duration_sec <= 0:
        total_duration_sec = len(lines) * _FALLBACK_SECONDS_PER_LINE
    total_chars = sum(len(line) for line in lines) or 1

    entries: list[str] = []
    cursor = 0.0
    for idx, line in enumerate(lines, start=1):
        share = len(line) / total_chars
        end = (
            total_duration_sec
            if idx == len(lines)
            else cursor + share * total_duration_sec
        )
        entries.append(
            f"{idx}\n{_fmt_ts(cursor)} --> {_fmt_ts(end)}\n{line}\n"
        )
        cursor = end
    return "\n".join(entries)


class TimedScene(Protocol):
    """具备实测时间轴的一幕（``VideoScene`` 满足此协议）。

    用协议而不是直接 import ``models.VideoScene``：字幕模块不绑死领域模型，
    任何「有起止秒与文本」的对象（含测试替身）都能用。
    """

    start_sec: float
    duration_sec: float
    text: str


def build_srt_from_scenes(scenes: Sequence[TimedScene]) -> str:
    """按**实测**时间轴构造 SRT（字幕与配音对齐的唯一正确做法）。

    Args:
        scenes: 有 ``start_sec`` / ``duration_sec`` / ``text`` 的幕，按幕序。

    Returns:
        SRT 全文；空列表 → 空串。
    """
    entries: list[str] = []
    for idx, scene in enumerate(scenes, start=1):
        start = max(0.0, float(scene.start_sec))
        end = start + max(0.0, float(scene.duration_sec))
        text = (scene.text or "").strip()
        if not text:
            continue
        entries.append(f"{idx}\n{_fmt_ts(start)} --> {_fmt_ts(end)}\n{text}\n")
    return "\n".join(entries)


def probe_audio_duration(audio_path: str | Path) -> float:
    """读取音频总时长（秒）；WAV 用标准库解析，其它/失败返回 0.0。"""
    path = Path(audio_path)
    if not path.is_file():
        return 0.0
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                rate = w.getframerate()
                return w.getnframes() / rate if rate > 0 else 0.0
        except (wave.Error, OSError, EOFError):
            return 0.0
    return 0.0


def write_srt_text(video_path: str | Path, content: str) -> str:
    """把已构造好的 SRT 内容写到视频同目录；返回其绝对路径。"""
    srt_path = Path(video_path).with_suffix(".srt")
    srt_path.write_text(content, encoding="utf-8")
    return str(srt_path)


def write_srt_sidecar(
    video_path: str | Path, text: str, total_duration_sec: float
) -> str:
    """在视频同目录写 ``.srt`` sidecar（**等比分配**退路）；返回绝对路径。"""
    return write_srt_text(video_path, build_srt(text, total_duration_sec))
