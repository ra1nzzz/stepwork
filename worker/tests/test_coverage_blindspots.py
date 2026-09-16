"""覆盖补盲：``ingest/metadata`` 与 ``hotspot/mcp`` 的**成功/错误路径**。

跑 ``pytest --cov`` 找到的两处真实盲区（不是"可选依赖装不上"造成的低覆盖）：

1. :func:`worker.runtime.ingest.metadata._probe` 此前只有 ``extract_metadata_safe``
   一条"不抛错"断言，ffprobe 成功返回 JSON 后的**主流程**（duration /
   streams 里的 width / height / codec_name / 逐流 duration 兜底）零覆盖
   —— 这是每次素材导入都会走的路径。
2. :func:`worker.runtime.hotspot.mcp._resolve_connection` 显式传
   ``connection_id`` 的三条错误分支（不存在 / 协议不对 / 已停用）零覆盖 ——
   只有"自动挑一个 active MCP 连接"的默认路径被测过。

不引入真实 ffprobe / 数据库以外依赖：metadata 用 monkeypatch 替 subprocess.run
返回构造好的 ffprobe JSON；mcp 用内存 SQLite + 手工插 agent_connections 行。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from worker.runtime import ingest
from worker.runtime.commands.bus import DispatchError
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.hotspot import mcp as hotspot_mcp

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _deps(conn: Any) -> Deps:
    """hotspot.mcp.resolve_connection 接 Deps 而非裸 conn。"""
    return Deps(repos=Repos(conn), ingest=None, asr=None, ai=None)


# ---------------------------------------------------------------------------
# ingest.metadata._probe 主流程
# ---------------------------------------------------------------------------


class _FakeProc:
    """subprocess.CompletedProcess 的最小替身。"""

    def __init__(self, returncode: int, stdout: str, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _probe_with(monkeypatch: pytest.MonkeyPatch, response: _FakeProc) -> dict[str, Any]:
    """把 subprocess.run 换成返回 response；返回 extract_metadata 的结果。"""
    monkeypatch.setattr(
        "worker.runtime.ingest.metadata.subprocess.run", lambda *a, **k: response
    )
    return ingest.metadata.extract_metadata("file:///does/not/matter.mp4")


def test_probe_extracts_duration_and_video_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ffprobe 正常返回时的主流程：format.duration + video stream 的
    width/height/codec_type 都要落到 metadata dict 里。"""
    fake = _FakeProc(
        returncode=0,
        stdout=json.dumps({
            "format": {"duration": "12.34"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "duration": "12.30",
                },
                {"codec_type": "audio", "codec_name": "aac", "duration": "12.40"},
            ],
        }),
    )
    meta = _probe_with(monkeypatch, fake)
    assert meta["duration_sec"] == pytest.approx(12.34)
    assert meta["width"] == 1080
    assert meta["height"] == 1920
    # 实现里把 codec_type 写进 codec_name（既有语义；不测就等于放过漂移）
    assert meta["codec_name"] == "video"


def test_probe_falls_back_to_stream_duration_when_format_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """format.duration 缺失时（部分容器不写顶层 duration），走 per-stream
    duration 兜底 —— 之前完全没测过，一旦有人重排这段逻辑，只有真数据会踩到。"""
    fake = _FakeProc(
        returncode=0,
        stdout=json.dumps({
            "format": {},  # 无 duration
            "streams": [
                {"codec_type": "audio", "duration": "8.75"},
            ],
        }),
    )
    meta = _probe_with(monkeypatch, fake)
    assert meta["duration_sec"] == pytest.approx(8.75)


def test_probe_ignores_non_numeric_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ffprobe 有时返回 ``N/A``；float() 会抛，走 except 分支不写入。
    没测过 = 未来有人加了 try/except 的兄弟逻辑就可能崩。"""
    fake = _FakeProc(
        returncode=0,
        stdout=json.dumps({
            "format": {"duration": "N/A"},
            "streams": [],
        }),
    )
    meta = _probe_with(monkeypatch, fake)
    assert "duration_sec" not in meta


def test_probe_returns_empty_when_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ffprobe 非零退出（媒体损坏等）→ ``{}``；``extract_metadata``
    降级保留 uri/ext，不抛错。"""
    fake = _FakeProc(returncode=1, stdout="", stderr="Invalid data found")
    meta = _probe_with(monkeypatch, fake)
    # 关键契约：不抛 + 保留最小字段
    assert meta["uri"] == "file:///does/not/matter.mp4"
    assert meta["ext"] == ".mp4"
    assert "duration_sec" not in meta


def test_probe_returns_empty_when_stdout_is_bad_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ffprobe 偶尔会打日志到 stdout 里污染 JSON；解析失败降级 ``{}``。"""
    fake = _FakeProc(returncode=0, stdout="[info] codec detected\nnot-json")
    meta = _probe_with(monkeypatch, fake)
    assert "duration_sec" not in meta


# ---------------------------------------------------------------------------
# hotspot.mcp._resolve_connection 显式 connection_id 的三条错误分支
# ---------------------------------------------------------------------------


def _seed_connection(
    conn: Any,
    *,
    conn_id: str,
    protocol: str = "mcp-client",
    status: str = "active",
) -> None:
    conn.execute(
        "INSERT INTO agent_connections "
        "(id, protocol, endpoint_or_command, local_or_remote, trust_level, "
        "auth_ref, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            conn_id, protocol, "/usr/bin/false", "local", "trusted",
            None, status,
            datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()


def test_resolve_connection_returns_active_mcp_record() -> None:
    """显式传连接 id + 协议对得上 + active → 返回该记录（此前只测过默认路径）。"""
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    _seed_connection(conn, conn_id="c-ok")
    record = hotspot_mcp.resolve_connection(_deps(conn), connection_id="c-ok")
    assert record is not None
    assert record["id"] == "c-ok"


def test_resolve_connection_not_found_raises() -> None:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    with pytest.raises(DispatchError) as ei:
        hotspot_mcp.resolve_connection(_deps(conn), connection_id="nope")
    assert ei.value.code == "NOT_FOUND"


def test_resolve_connection_wrong_protocol_raises() -> None:
    """指定了 id 但那行是 a2a / acp 连接 —— 不能默认拿它当 MCP 用。"""
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    _seed_connection(conn, conn_id="c-a2a", protocol="a2a")
    with pytest.raises(DispatchError) as ei:
        hotspot_mcp.resolve_connection(_deps(conn), connection_id="c-a2a")
    assert ei.value.code == "INVALID_ARGUMENT"
    assert "MCP" in ei.value.message


def test_resolve_connection_disabled_raises() -> None:
    """协议对但 status != active：不能拿停用连接发请求。"""
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    _seed_connection(conn, conn_id="c-off", status="disabled")
    with pytest.raises(DispatchError) as ei:
        hotspot_mcp.resolve_connection(_deps(conn), connection_id="c-off")
    assert ei.value.code == "CONNECTION_DISABLED"
