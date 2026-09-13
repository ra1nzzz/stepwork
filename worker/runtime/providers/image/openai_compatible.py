"""OpenAI 兼容文生图适配器 —— S2 配图阶段的**第一个真实厂商实现**。

覆盖 ``POST {base_url}/images/generations`` 这一份契约的所有厂商：

- 硅基流动 SiliconFlow（``Kwai-Kolors/Kolors`` / ``FLUX.1-schnell`` …）
- 智谱 CogView-4 / ``glm-image``（``open.bigmodel.cn/api/paas/v4``）
- OpenAI 自身，以及任何 OpenAI 兼容网关（one-api / new-api / 自建转发）

**刻意不覆盖**：阿里云通义万相 / ``qwen-image``。官方文档明确写着「图像
生成模型通过 DashScope 原生 API 调用，**不支持** OpenAI 兼容
（compatible-mode）」—— 端点不同（``/api/v1/services/aigc/...``）、尺寸
写法不同（``W*H`` 星号分隔）、响应结构不同
（``output.choices[0].message.content[0].image``）。硬塞进本文件只会让两边
都别扭，真选了它再单开一个适配器。

**为什么是一个通用适配器而不是每家一个文件**：三家候选里有两家是同一份
契约，第三家（阿里）根本不是。按「契约」而不是按「公司」切文件，换厂商
只改一个 env。真出现第二份契约时再拆，而不是现在先铺三个空壳。

响应解析容忍两种形状：``data`` 或 ``images`` 数组，元素里 ``b64_json``
优先（省一次下载），否则 ``url`` / ``image_url`` / ``image`` 取直链再 GET。
后者必须**立刻下载落盘**：厂商直链普遍有有效期（智谱 30 天、硅基流动
10 分钟），把 url 直接存进 ``video_scenes.image_uri`` 渲片时会变裂图。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import tempfile
from typing import Any
from urllib.parse import urlparse

from worker.runtime.net import make_async_client
from worker.runtime.providers.image.base import ImageProviderError

#: 生图比聊天慢得多（CogView hd 约 20s），留足余量
_DEFAULT_TIMEOUT = 120.0
_DEFAULT_OUT_DIR = os.path.join(tempfile.gettempdir(), "stepwork_images")

_EXT_BY_CONTENT_TYPE: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/bmp": "bmp",
}
#: 响应体里可能装图的字段名，按顺序试（``data`` 是 OpenAI 标准，``images``
#: 是部分国产网关的写法）
_PAYLOAD_ARRAY_KEYS = ("data", "images", "results")
#: 元素里可能装图的字段名
_ITEM_URL_KEYS = ("url", "image_url", "image")


def _strip_data_uri(b64: str) -> str:
    """去掉 ``data:image/png;base64,`` 前缀（部分网关会带）。"""
    marker = "base64,"
    idx = b64.find(marker)
    return b64[idx + len(marker) :] if idx >= 0 else b64


def _ext_from(content_type: str, url: str) -> str:
    """按 Content-Type 定扩展名，退回 URL 后缀，再退回 png。

    扩展名不是小事：Chromium 虽然会嗅探，但 ``file://`` 下嗅探失败就会
    裂图，而裂图在成片里**看不出来是配图坏了**。
    """
    ct = content_type.split(";")[0].strip().lower()
    if ct in _EXT_BY_CONTENT_TYPE:
        return _EXT_BY_CONTENT_TYPE[ct]
    suffix = urlparse(url).path.rsplit(".", 1)[-1].lower()
    if suffix == "jpeg":
        return "jpg"
    if suffix in _EXT_BY_CONTENT_TYPE.values():
        return suffix
    return "png"


def _first_image(payload: Any) -> tuple[str | None, str | None]:
    """从响应体里取第一张图：``(b64_json, url)``，取不到则 ``(None, None)``。

    **不抛解析异常**：响应形状千奇百怪，抛 KeyError 只会让错误信息变成
    ``KeyError: 'data'``，看不出是哪家、为什么。统一交给调用方抛带 body
    片段的 :class:`ImageProviderError`。
    """
    if not isinstance(payload, dict):
        return None, None
    items: Any = None
    for key in _PAYLOAD_ARRAY_KEYS:
        value = payload.get(key)
        if isinstance(value, list) and value:
            items = value
            break
    if items is None:
        return None, None
    first = items[0]
    if not isinstance(first, dict):
        return None, None
    b64 = first.get("b64_json") or first.get("b64")
    if isinstance(b64, str) and b64.strip():
        return b64.strip(), None
    for key in _ITEM_URL_KEYS:
        url = first.get(key)
        if isinstance(url, str) and url.strip():
            return None, url.strip()
    return None, None


def _write_atomic(path: str, data: bytes) -> None:
    """先写 ``.part`` 再原子改名：中断/并发不会留下半截图被当有效缓存。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


class OpenAICompatibleImageProvider:
    """OpenAI 兼容 ``/images/generations`` 文生图 Provider。"""

    name = "openai-compatible-image"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str | None = None,
        size: str = "1024x1024",
        *,
        size_key: str = "size",
        client: Any = None,
        extra_params: dict[str, Any] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.size = size
        #: 尺寸字段名：OpenAI 标准是 ``size``，硅基流动文档写的是
        #: ``image_size``。型号差异导致的「尺寸没生效」最难查（图照样出，
        #: 只是比例不对），所以做成可配置而不是硬猜。
        self.size_key = size_key
        self._client = client
        self.extra_params = dict(extra_params or {})
        self.timeout = timeout

    def _resolve_size(self, opts: dict[str, Any]) -> str:
        """尺寸优先级：opts 的 width/height > opts 的 size 串 > 构造默认值。

        handler 传的是 ``{"width":..,"height":..}``（来自 payload.size），
        这是唯一能让 per-request 指定比例的路子。
        """
        width = opts.get("width")
        height = opts.get("height")
        if isinstance(width, int) and isinstance(height, int) and width and height:
            return f"{width}x{height}"
        raw = opts.get("size")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return self.size

    def _build_body(self, prompt: str, size: str, opts: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"prompt": prompt, self.size_key: size, "n": 1}
        if self.model:
            body["model"] = self.model
        body.update(self.extra_params)
        params = opts.get("params")
        if isinstance(params, dict):
            body.update(params)
        return body

    def _target_path(self, prompt: str, size: str, ext: str, out_dir: str) -> str:
        """同（提示词, 型号, 尺寸）→ 同文件名：重跑可复用，不重复计费。"""
        key = f"{prompt}\x00{self.model}\x00{size}".encode()
        digest = hashlib.sha256(key).hexdigest()[:16]
        return os.path.join(out_dir, f"img_{digest}.{ext}")

    async def generate(self, prompt: str, opts: dict[str, Any] | None = None) -> str:
        opts = opts or {}
        size = self._resolve_size(opts)
        out_dir = str(opts.get("out_dir") or _DEFAULT_OUT_DIR)
        body = self._build_body(prompt, size, opts)
        url = f"{self.base_url}/images/generations"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        client = self._client or make_async_client()
        async with client as c:
            resp = await c.post(url, headers=headers, json=body, timeout=self.timeout)
            if resp.status_code >= 400:
                raise ImageProviderError(
                    f"HTTP {resp.status_code} from {url}: {resp.text[:300]}"
                )
            try:
                payload = resp.json()
            except ValueError:
                raise ImageProviderError(
                    f"non-JSON response from {url}: {resp.text[:300]}"
                ) from None
            b64, image_url = _first_image(payload)
            if b64 is not None:
                data = await asyncio.to_thread(
                    base64.b64decode, _strip_data_uri(b64)
                )
                ext = "png"
            elif image_url:
                img = await c.get(image_url, timeout=self.timeout)
                if img.status_code >= 400:
                    raise ImageProviderError(
                        f"HTTP {img.status_code} downloading image "
                        f"{image_url}: {img.text[:200]}"
                    )
                data = img.content
                ext = _ext_from(img.headers.get("content-type", ""), image_url)
            else:
                raise ImageProviderError(
                    f"no image in response from {url}: "
                    f"{str(payload)[:300]}"
                )

        if not data:
            raise ImageProviderError(f"empty image bytes from {url}")
        path = self._target_path(prompt, size, ext, out_dir)
        await asyncio.to_thread(_write_atomic, path, data)
        return "file://" + path
