"""Batch 1：素材导入原语（hash + metadata + is_media）测试。"""

from __future__ import annotations

from pathlib import Path

from worker.runtime import ingest
from worker.runtime.ingest.hash import hash_bytes, hash_file


def _tmp_media(tmp_path: Path) -> Path:
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00\x01binarycontent\x02\x03")
    return p


def test_hash_file_deterministic(tmp_path: Path) -> None:
    p = _tmp_media(tmp_path)
    h1 = hash_file(p)
    h2 = hash_file(str(p))
    assert h1 == h2
    assert len(h1) == 64


def test_hash_bytes() -> None:
    assert hash_bytes(b"abc") == hash_bytes(b"abc")
    assert hash_bytes(b"abc") != hash_bytes(b"xyz")


def test_is_media() -> None:
    assert ingest.is_media("a.mp4")
    assert ingest.is_media("a.MP4")
    assert not ingest.is_media("a.txt")


def test_extract_metadata_safe(tmp_path: Path) -> None:
    p = _tmp_media(tmp_path)
    meta = ingest.extract_metadata(str(p))
    assert meta["ext"] == ".mp4"
    assert "size_bytes" in meta
    # 无论是否有 ffprobe，都不应抛错（头脑风暴 P1 降级）
    assert isinstance(meta.get("duration_sec", 0.0), (float, type(None)))
