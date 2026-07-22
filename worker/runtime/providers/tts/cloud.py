"""云端 TTS（env 接线，零硬编码密钥，W6）。

按头脑风暴 P0：``api_key`` / ``base_url`` / ``model`` 仅来自 env 或显式
传入的 config；缺失必要配置时由 resolve 返回 ``None``（handler 转译为
``UNAVAILABLE``），绝不把空密钥打到线上。
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import Any

import httpx


def _write_bytes(path: Path, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


class CloudTTSProvider:
    """云端 TTS（OpenAI-compatible / 各厂商端点）。"""

    name = "cloud-tts"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str | None = None,
        client: Any = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = client

    async def synthesize(
        self, text: str, opts: dict[str, Any] | None = None
    ) -> str:
        out_dir = (opts or {}).get("out_dir") or (
            Path(tempfile.gettempdir()) / "stepwork_tts"
        )
        await asyncio.to_thread(_ensure_dir, out_dir)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        path = Path(out_dir) / f"tts_{digest}.wav"
        client = self._client or httpx.AsyncClient()
        async with client as c:
            resp = await c.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"text": text, "model": self.model},
                timeout=30,
            )
            resp.raise_for_status()
            audio = resp.content
        await asyncio.to_thread(_write_bytes, path, audio)
        return "file://" + str(path)
