"""Tranche 2：链接导入（PRD-SRC-002/003）测试。

覆盖：

1. ``download_url``（mock httpx）三态：成功 / NEED_LOGIN(401/403) /
   DOWNLOAD_FAILED(404/网络错误)。
2. 大小上限（流式二次校验）。
3. content-type → 扩展名映射。
4. dispatch 级 ImportSource {url}：job(DOWNLOADING) 成功链路 +
   NEED_LOGIN 失败落 job FAILED。
5. 来源记录：original_url / author / rights_declaration 持久化 +
   非法 rights 拒绝。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from worker.runtime import ingest
from worker.runtime.commands.bus import DispatchError, dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.ingest.download import DownloadResult, download_url

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )


def _is_file(path: Path) -> bool:
    """同步 helper：检查文件存在性（避免 async 测试函数内触发 ASYNC240）。"""
    return path.is_file()


def _read_bytes(path: Path) -> bytes:
    """同步 helper：读文件字节（避免 async 测试函数内触发 ASYNC240）。"""
    return path.read_bytes()


def _part_files(directory: Path) -> list[Path]:
    """同步 helper：列出 .part 残留（避免 async 测试函数内触发 ASYNC240）。"""
    return list(directory.glob("*.part"))


def _write_fake_download(dest_dir: Any) -> Path:
    """同步 helper：写一个伪下载文件（避免 async 内触发 ASYNC240）。"""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    f = dest / "dl_test_clip.mp4"
    f.write_bytes(b"downloaded-bytes")
    return f


def _deps() -> Deps:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    return Deps(repos=Repos(conn), ingest=ingest)


def _env(
    command_type: str,
    payload: dict[str, Any] | None = None,
    workspace_id: str = "ws-dl",
) -> dict[str, Any]:
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": workspace_id,
        "payload": payload or {},
        "requestedAt": datetime.now(UTC).isoformat(),
    }


async def test_download_success_with_ct_ext(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"fake-video-bytes",
            headers={"content-type": "video/mp4"},
        )

    async with _client(handler) as client:
        result = await download_url(
            "https://example.com/media/clip", tmp_path, client=client
        )
    assert result.size_bytes == len(b"fake-video-bytes")
    path = Path(result.local_path)
    assert _is_file(path)
    assert path.suffix == ".mp4"  # content-type → 扩展名映射
    assert path.parent == tmp_path
    assert _read_bytes(path) == b"fake-video-bytes"
    # 无残留 .part 中间文件
    assert _part_files(tmp_path) == []


@pytest.mark.parametrize("status", [401, 403])
async def test_download_need_login(tmp_path: Path, status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async with _client(handler) as client:
        with pytest.raises(DispatchError) as exc:
            await download_url("https://example.com/f.mp4", tmp_path, client=client)
    assert exc.value.code == "NEED_LOGIN"


@pytest.mark.parametrize("status", [404, 500])
async def test_download_http_error_failed(tmp_path: Path, status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async with _client(handler) as client:
        with pytest.raises(DispatchError) as exc:
            await download_url("https://example.com/f.mp4", tmp_path, client=client)
    assert exc.value.code == "DOWNLOAD_FAILED"


async def test_download_network_error_failed(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with _client(handler) as client:
        with pytest.raises(DispatchError) as exc:
            await download_url("https://example.com/f.mp4", tmp_path, client=client)
    assert exc.value.code == "DOWNLOAD_FAILED"


async def test_download_size_cap(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100)

    async with _client(handler) as client:
        with pytest.raises(DispatchError) as exc:
            await download_url(
                "https://example.com/big.bin", tmp_path,
                max_bytes=10, client=client,
            )
    assert exc.value.code == "DOWNLOAD_FAILED"
    assert "too large" in exc.value.message
    # 超限中间文件已清理
    assert _part_files(tmp_path) == []


async def test_download_invalid_url(tmp_path: Path) -> None:
    with pytest.raises(DispatchError) as exc:
        await download_url("ftp://example.com/f", tmp_path)
    assert exc.value.code == "DOWNLOAD_FAILED"


async def test_import_source_url_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dispatch 级：{url} 导入 → job(DOWNLOADING) → 资产落库（含 ffprobe 链路）。"""
    from worker.runtime.handlers import import_source as mod

    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))

    async def fake_download(
        url: str, dest_dir: Any, max_bytes: Any = None, client: Any = None
    ) -> DownloadResult:
        f = _write_fake_download(dest_dir)
        return DownloadResult(
            local_path=str(f), content_type="video/mp4", size_bytes=16
        )

    monkeypatch.setattr(mod, "download_url", fake_download)
    deps = _deps()
    res = await dispatch(
        _env(
            "ImportSource",
            {
                "url": "https://example.com/clip.mp4",
                "author": "作者甲",
                "rights_declaration": "licensed",
            },
        ),
        deps,
    )
    assert res["ok"] is True, res.get("error")
    assert res["detail"]["dedup"] is False
    assert res["job_id"]

    job = deps.repos.jobs.get(res["job_id"])
    assert job is not None
    assert job.state.value == "succeeded"
    assert job.stage is not None and job.stage.value == "downloading"

    asset = deps.repos.source_assets.get(res["artifact_ids"][0])
    assert asset is not None
    assert asset.original_uri == "https://example.com/clip.mp4"
    assert asset.rights_declaration == "licensed"
    assert asset.metadata.get("author") == "作者甲"
    # 既有 ffprobe/metadata 链路（降级最小字段也含 size_bytes/ext）
    assert asset.metadata.get("size_bytes") == 16
    # 下载落在 $STEPWORK_HOME/assets/<project_id>/ 内
    assert str(tmp_path) in asset.local_uri


async def test_import_source_url_need_login_fails_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from worker.runtime.handlers import import_source as mod

    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))

    async def fake_download(
        url: str, dest_dir: Any, max_bytes: Any = None, client: Any = None
    ) -> DownloadResult:
        raise DispatchError("NEED_LOGIN", "HTTP 403")

    monkeypatch.setattr(mod, "download_url", fake_download)
    deps = _deps()
    res = await dispatch(
        _env("ImportSource", {"url": "https://example.com/private.mp4"}), deps
    )
    assert res["ok"] is False
    assert "NEED_LOGIN" in res["error"]
    # job 落 FAILED 终态，error_code=NEED_LOGIN
    row = deps.repos.conn.execute(
        "SELECT state, error_code FROM jobs WHERE job_type='import'"
    ).fetchone()
    assert row["state"] == "failed"
    assert row["error_code"] == "NEED_LOGIN"


async def test_import_source_url_and_local_exclusive() -> None:
    deps = _deps()
    res = await dispatch(
        _env(
            "ImportSource",
            {"url": "https://x/y.mp4", "local_uri": "file://a.mp4"},
        ),
        deps,
    )
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]


async def test_import_source_local_rights_fields() -> None:
    """本地导入路径同样持久化 original_url/author/rights_declaration。"""
    deps = _deps()
    res = await dispatch(
        _env(
            "ImportSource",
            {
                "local_uri": "file://a.mp4",
                "content_hash": "h_rights",
                "original_url": "https://source.example/v",
                "author": "作者乙",
                "rights_declaration": "reference",
            },
        ),
        deps,
    )
    assert res["ok"] is True, res.get("error")
    asset = deps.repos.source_assets.get(res["artifact_ids"][0])
    assert asset is not None
    assert asset.original_uri == "https://source.example/v"
    assert asset.rights_declaration == "reference"
    assert asset.metadata.get("author") == "作者乙"


async def test_import_source_invalid_rights_rejected() -> None:
    deps = _deps()
    res = await dispatch(
        _env(
            "ImportSource",
            {
                "local_uri": "file://a.mp4",
                "content_hash": "h_bad",
                "rights_declaration": "stolen",
            },
        ),
        deps,
    )
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]
