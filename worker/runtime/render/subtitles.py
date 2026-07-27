"""SRT 字幕 sidecar 生成（Tranche 2，PRD-REN-003）。

按脚本行/句拆分，时长按音频总时长**等比**（字符量占比）分配；输出为
合法 SRT（序号 / ``HH:MM:SS,mmm --> HH:MM:SS,mmm`` / 文本 / 空行）。
"""

from __future__ import annotations

import re
import wave
from pathlib import Path

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


def write_srt_sidecar(
    video_path: str | Path, text: str, total_duration_sec: float
) -> str:
    """在视频同目录写 ``.srt`` sidecar；返回其绝对路径。"""
    video = Path(video_path)
    srt_path = video.with_suffix(".srt")
    srt_path.write_text(build_srt(text, total_duration_sec), encoding="utf-8")
    return str(srt_path)
