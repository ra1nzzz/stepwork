"""媒体元数据提取（W3，Batch 1）。

设计原则（三角色头脑风暴 P1）：
- 尽力抽取时长 / 分辨率 / 编码等；任何失败都**降级**为最小安全字段，
  绝不向上抛异常导致导入链路崩溃。
- 离线环境通常无 ``ffprobe``，此时仅返回 ``size_bytes`` / ``ext`` 等
  从文件系统即可获得的信息。
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

_MEDIA_EXTS = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac",
}


def _ext(uri: str) -> str:
    _, ext = os.path.splitext(uri)
    return ext.lower()


def is_media(uri: str) -> bool:
    """``uri`` 是否看起来是受支持的媒体文件。"""
    return _ext(uri) in _MEDIA_EXTS


def extract_metadata(local_uri: str) -> dict[str, Any]:
    """提取媒体元数据；任何失败都降级为最小安全字段。

    Returns:
        dict：至少含 ``uri`` / ``ext``；存在文件时含 ``size_bytes``；
        若 ``ffprobe`` 可用则附加 ``duration_sec`` / ``codec_name`` /
        ``width`` / ``height``。
    """
    meta: dict[str, Any] = {"uri": local_uri, "ext": _ext(local_uri)}
    try:
        if os.path.exists(local_uri):
            meta["size_bytes"] = os.path.getsize(local_uri)
    except OSError:
        pass
    try:
        meta.update(_probe(local_uri))
    except Exception:
        # 降级：保留 size/ext，不向上抛（头脑风暴 P1）
        pass
    return meta


def _probe(local_uri: str) -> dict[str, Any]:
    """调用 ``ffprobe`` 抽取结构化元数据；缺失/失败返回 ``{}``。"""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", local_uri,
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return {}
    if out.returncode != 0 or not out.stdout.strip():
        return {}
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return {}

    res: dict[str, Any] = {}
    fmt = data.get("format", {})
    dur = fmt.get("duration")
    if dur is not None:
        try:
            res["duration_sec"] = float(dur)
        except (TypeError, ValueError):
            pass
    for s in data.get("streams", []):
        ctype = s.get("codec_type")
        if ctype in ("video", "audio") and "codec_name" not in res:
            res["codec_name"] = s.get("codec_type")
        if ctype == "video":
            res["width"] = s.get("width")
            res["height"] = s.get("height")
        if "duration_sec" not in res and s.get("duration"):
            try:
                res["duration_sec"] = float(s["duration"])
            except (TypeError, ValueError):
                pass
    return res
