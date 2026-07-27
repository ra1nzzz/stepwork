"""直链 URL 下载（Tranche 2，PRD-SRC-002）。

httpx 流式下载**直链文件**到目标目录；三态错误契约：

- HTTP 401/403，或最终响应 content-type 为 ``text/html``（登录墙/验证页）
  → ``DispatchError("NEED_LOGIN", ...)``（需要登录）
- 其它 4xx/5xx / 网络错误 / 超限 → ``DispatchError("DOWNLOAD_FAILED", ...)``
- 成功 → :class:`DownloadResult`

安全边界（P0）：**只做直链 HTTP(S) 下载**，禁止任何平台反爬/风控规避
逻辑。大小上限默认 2GiB（``STEPWORK_MAX_DOWNLOAD_BYTES`` 可配），
header 与流式双重校验，防止 Content-Length 缺失/谎报导致磁盘打满。
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from worker.runtime.commands.bus import DispatchError

DEFAULT_MAX_DOWNLOAD_BYTES: int = 2 * 1024 * 1024 * 1024
"""下载大小上限缺省值（2 GiB）。"""

_CHUNK_SIZE: int = 1024 * 256

# content-type → 扩展名映射（直链常见媒体/文档类型）
_CT_EXT_MAP: dict[str, str] = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/aac": ".aac",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class DownloadResult:
    """一次直链下载的结果。"""

    local_path: str
    content_type: str | None
    size_bytes: int


def resolve_max_bytes(explicit: int | None = None) -> int:
    """解析下载大小上限：显式参数 > ``STEPWORK_MAX_DOWNLOAD_BYTES`` > 默认 2GiB。"""
    if explicit is not None and explicit > 0:
        return explicit
    raw = os.environ.get("STEPWORK_MAX_DOWNLOAD_BYTES")
    if raw:
        try:
            parsed = int(raw)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return DEFAULT_MAX_DOWNLOAD_BYTES


def _base_content_type(content_type: str | None) -> str:
    """取 content-type 主类型（去参数、去空白、小写）；``None`` 归空串。"""
    return (content_type or "").split(";", 1)[0].strip().lower()


def kind_for_content_type(content_type: str | None) -> str:
    """按最终响应 content-type 推断资产 kind（PRD-SRC-002）。

    ``video/*`` → video，``audio/*`` → audio，``image/*`` → image，
    其余（含缺失）→ document。
    """
    base_ct = _base_content_type(content_type)
    for prefix, kind in (
        ("video/", "video"),
        ("audio/", "audio"),
        ("image/", "image"),
    ):
        if base_ct.startswith(prefix):
            return kind
    return "document"


def _ext_for(content_type: str | None, url: str) -> str:
    """按 content-type 映射扩展名；未知类型回退 URL 路径后缀，再回退 ``.bin``。"""
    base_ct = _base_content_type(content_type)
    if base_ct in _CT_EXT_MAP:
        return _CT_EXT_MAP[base_ct]
    path_ext = os.path.splitext(urlparse(url).path)[1]
    if path_ext and len(path_ext) <= 8:
        return path_ext.lower()
    return ".bin"


def _dest_filename(url: str, ext: str) -> str:
    """从 URL 派生落盘文件名（短 uuid 前缀防碰撞 + 白名单字符清洗）。"""
    base = os.path.basename(urlparse(url).path) or "download"
    stem = os.path.splitext(base)[0]
    safe_stem = _SAFE_NAME_RE.sub("_", stem)[:64] or "download"
    return f"dl_{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"


def _prepare_dest(dest_dir: str | Path) -> Path:
    """同步 helper：确保目标目录存在（避免 async 内触发 ASYNC240）。"""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _open_for_write(path: Path) -> Any:
    """同步 helper：打开写句柄（避免 async 内触发 ASYNC230）。"""
    return open(path, "wb")


async def download_url(
    url: str,
    dest_dir: str | Path,
    max_bytes: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> DownloadResult:
    """流式下载直链文件到 ``dest_dir``。

    Args:
        url: http/https 直链。
        dest_dir: 目标目录（不存在则创建）。
        max_bytes: 大小上限；缺省经 :func:`resolve_max_bytes` 解析。
        client: 可注入的 ``httpx.AsyncClient``（测试 mock 用）；未注入时
            内部构造并负责关闭。

    Returns:
        :class:`DownloadResult`。

    Raises:
        DispatchError: ``NEED_LOGIN``（401/403）/ ``DOWNLOAD_FAILED``（其它
            HTTP 错误、网络错误、超限、非 http(s) URL）。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise DispatchError("DOWNLOAD_FAILED", f"invalid url: {url!r}")

    limit = resolve_max_bytes(max_bytes)
    dest = _prepare_dest(dest_dir)

    own_client = client is None
    http = client or httpx.AsyncClient(
        follow_redirects=True, timeout=httpx.Timeout(30.0)
    )
    tmp_path: Path | None = None
    try:
        async with http.stream("GET", url) as resp:
            if resp.status_code in (401, 403):
                raise DispatchError(
                    "NEED_LOGIN",
                    f"HTTP {resp.status_code}: source requires login/authorization",
                )
            if resp.status_code >= 400:
                raise DispatchError(
                    "DOWNLOAD_FAILED", f"HTTP {resp.status_code} for {url}"
                )

            content_type = resp.headers.get("content-type")
            # 登录墙识别：最终响应（重定向后）是 HTML 页面 → 不是直链媒体，
            # 多半是登录/验证页；直接拒绝，绝不落一个伪装成媒体的 .html 资产
            if _base_content_type(content_type) == "text/html":
                raise DispatchError(
                    "NEED_LOGIN", "目标返回了 HTML 页面，可能需要登录"
                )
            declared = resp.headers.get("content-length")
            if declared is not None:
                try:
                    if int(declared) > limit:
                        raise DispatchError(
                            "DOWNLOAD_FAILED",
                            f"file too large: content-length {declared} > {limit}",
                        )
                except ValueError:
                    pass

            ext = _ext_for(content_type, str(resp.url))
            final_path = dest / _dest_filename(str(resp.url), ext)
            tmp_path = final_path.with_suffix(final_path.suffix + ".part")

            size = 0
            with _open_for_write(tmp_path) as f:
                async for chunk in resp.aiter_bytes(_CHUNK_SIZE):
                    size += len(chunk)
                    # 流式二次校验：Content-Length 缺失/谎报也不越限
                    if size > limit:
                        raise DispatchError(
                            "DOWNLOAD_FAILED",
                            f"file too large: streamed {size} > {limit}",
                        )
                    f.write(chunk)
            os.replace(tmp_path, final_path)
            tmp_path = None
            return DownloadResult(
                local_path=str(final_path),
                content_type=content_type,
                size_bytes=size,
            )
    except DispatchError:
        raise
    except httpx.HTTPError as e:
        raise DispatchError("DOWNLOAD_FAILED", f"network error: {e}") from None
    except OSError as e:
        raise DispatchError("DOWNLOAD_FAILED", f"disk error: {e}") from None
    finally:
        # 失败清理：残留 .part 中间文件不留盘（配合 retention 清扫兜底）
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        if own_client:
            await http.aclose()
