"""OpenAI 兼容配图 Provider 测试（S2 最后一段：厂商适配器）。

锁死四件事：

1. **响应形状的容忍度**：``data``/``images`` 数组、``b64_json``/``url``/
   ``image_url`` 都能取到图 —— 厂商文档和实际返回常有出入，解析层必须
   宽容，但**取不到时必须报错**，不能静默返回空文件；
2. **直链必须立刻落盘**：厂商直链有有效期（10 分钟 ~ 30 天），把 url 存进
   ``image_uri`` 渲片时就是裂图。所以只要返回 url，就一定多一次 GET 落盘；
3. **错误信息带 body 片段**：只报 ``HTTP 400`` 等于让人猜，厂商的真实原因
   在 body 里；
4. **同输入 → 同文件名**（重跑可复用，不重复计费）。

同时覆盖 :func:`resolve_image` 的厂商预置接线。
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.providers.image.base import ImageProvider, ImageProviderError
from worker.runtime.providers.image.openai_compatible import (
    OpenAICompatibleImageProvider,
)
from worker.runtime.providers.resolve import IMAGE_PRESETS, resolve_image

_PNG = b"\x89PNG\r\n\x1a\nfake-bytes"


class _FakeResponse:
    """最小 httpx.Response 替身（只实现被测代码用到的属性）。"""

    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        text: str = "",
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = content
        self.headers = headers or {}

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    """最小 httpx.AsyncClient 替身；记录调用以便断言请求体。"""

    def __init__(
        self, post_resp: _FakeResponse, get_resp: _FakeResponse | None = None
    ) -> None:
        self.post_resp = post_resp
        self.get_resp = get_resp
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self.get_calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.post_calls.append((url, kwargs))
        return self.post_resp

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.get_calls.append((url, kwargs))
        if self.get_resp is None:
            raise AssertionError(f"unexpected GET {url}")
        return self.get_resp


def _is_file(path: str) -> bool:
    """同步 helper：避免 async 测试内触发 ASYNC240。"""
    return os.path.isfile(path)


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _provider(client: _FakeClient, **kw: Any) -> OpenAICompatibleImageProvider:
    params: dict[str, Any] = {
        "api_key": "k",
        "base_url": "https://img.example.com/v1",
        "model": "m1",
        "client": client,
    }
    params.update(kw)
    return OpenAICompatibleImageProvider(**params)


async def test_url_response_is_downloaded_and_saved(tmp_path: Path) -> None:
    """返回 url → 必须 GET 下来落盘，不能直接把 url 当 image_uri。"""
    client = _FakeClient(
        post_resp=_FakeResponse(payload={"data": [{"url": "https://cdn/x.png"}]}),
        get_resp=_FakeResponse(
            content=_PNG, headers={"content-type": "image/png"}
        ),
    )
    p = _provider(client)
    uri = await p.generate("一只猫。", {"out_dir": str(tmp_path)})

    assert uri.startswith("file://")
    path = uri.removeprefix("file://")
    assert _is_file(path)
    assert _read_bytes(path) == _PNG
    assert path.endswith(".png")  # 扩展名由 Content-Type 决定
    # 直链被真的下载了（不是原样返回）
    assert client.get_calls[0][0] == "https://cdn/x.png"
    body = client.post_calls[0][1]["json"]
    assert body["prompt"] == "一只猫。"
    assert body["model"] == "m1"
    assert body["size"] == "1024x1024"


async def test_b64_json_needs_no_download(tmp_path: Path) -> None:
    client = _FakeClient(
        post_resp=_FakeResponse(
            payload={"data": [{"b64_json": base64.b64encode(_PNG).decode()}]}
        )
    )
    uri = await _provider(client).generate("猫。", {"out_dir": str(tmp_path)})
    assert not client.get_calls  # b64 直接解码，省一次往返
    assert _read_bytes(uri.removeprefix("file://")) == _PNG


async def test_data_uri_prefix_is_stripped(tmp_path: Path) -> None:
    raw = "data:image/png;base64," + base64.b64encode(_PNG).decode()
    client = _FakeClient(post_resp=_FakeResponse(payload={"images": [{"b64_json": raw}]}))
    uri = await _provider(client).generate("猫。", {"out_dir": str(tmp_path)})
    assert _read_bytes(uri.removeprefix("file://")) == _PNG


async def test_alternate_array_and_url_keys(tmp_path: Path) -> None:
    """``images`` 数组 + ``image_url`` 字段（部分国产网关的写法）。"""
    client = _FakeClient(
        post_resp=_FakeResponse(payload={"images": [{"image_url": "https://c/a.jpg"}]}),
        get_resp=_FakeResponse(
            content=_PNG, headers={"content-type": "image/jpeg"}
        ),
    )
    uri = await _provider(client).generate("猫。", {"out_dir": str(tmp_path)})
    assert uri.endswith(".jpg")


async def test_size_from_opts_width_height(tmp_path: Path) -> None:
    """handler 传的 ``{"width","height"}`` 必须能决定出图比例。"""
    client = _FakeClient(
        post_resp=_FakeResponse(payload={"data": [{"b64_json": base64.b64encode(_PNG).decode()}]})
    )
    await _provider(client).generate(
        "猫。", {"out_dir": str(tmp_path), "width": 1080, "height": 1920}
    )
    assert client.post_calls[0][1]["json"]["size"] == "1080x1920"


async def test_size_key_is_configurable(tmp_path: Path) -> None:
    """硅基流动文档用 ``image_size``；字段名错了「图照样出、比例不对」。"""
    client = _FakeClient(
        post_resp=_FakeResponse(payload={"data": [{"b64_json": base64.b64encode(_PNG).decode()}]})
    )
    await _provider(client, size_key="image_size", size="720x1440").generate(
        "猫。", {"out_dir": str(tmp_path)}
    )
    body = client.post_calls[0][1]["json"]
    assert body["image_size"] == "720x1440"
    assert "size" not in body


async def test_extra_params_are_merged(tmp_path: Path) -> None:
    client = _FakeClient(
        post_resp=_FakeResponse(payload={"data": [{"b64_json": base64.b64encode(_PNG).decode()}]})
    )
    await _provider(client, extra_params={"watermark": False}).generate(
        "猫。", {"out_dir": str(tmp_path), "params": {"seed": 42}}
    )
    body = client.post_calls[0][1]["json"]
    assert body["watermark"] is False and body["seed"] == 42


async def test_http_error_carries_vendor_body(tmp_path: Path) -> None:
    """只报状态码等于让人猜；厂商把真实原因放在 body 里。"""
    client = _FakeClient(
        post_resp=_FakeResponse(
            status_code=400, text='{"error":{"message":"model m1 not supported"}}'
        )
    )
    with pytest.raises(ImageProviderError) as ei:
        await _provider(client).generate("猫。", {"out_dir": str(tmp_path)})
    assert "400" in str(ei.value)
    assert "model m1 not supported" in str(ei.value)


async def test_download_failure_is_visible(tmp_path: Path) -> None:
    client = _FakeClient(
        post_resp=_FakeResponse(payload={"data": [{"url": "https://cdn/x.png"}]}),
        get_resp=_FakeResponse(status_code=403, text="forbidden"),
    )
    with pytest.raises(ImageProviderError) as ei:
        await _provider(client).generate("猫。", {"out_dir": str(tmp_path)})
    assert "403" in str(ei.value) and "forbidden" in str(ei.value)


@pytest.mark.parametrize("payload", [{"data": []}, {"data": [{}]}, {"foo": 1}, {"data": "x"}])
async def test_no_image_in_response_raises(payload: Any, tmp_path: Path) -> None:
    """取不到图必须报错 —— 静默写空文件 = 成片里一张看不见的裂图。"""
    client = _FakeClient(post_resp=_FakeResponse(payload=payload))
    with pytest.raises(ImageProviderError) as ei:
        await _provider(client).generate("猫。", {"out_dir": str(tmp_path)})
    assert "no image" in str(ei.value)


async def test_non_json_response_raises(tmp_path: Path) -> None:
    client = _FakeClient(post_resp=_FakeResponse(text="<html>502</html>"))
    with pytest.raises(ImageProviderError) as ei:
        await _provider(client).generate("猫。", {"out_dir": str(tmp_path)})
    assert "non-JSON" in str(ei.value)


async def test_same_prompt_reuses_file(tmp_path: Path) -> None:
    """同（提示词, 型号, 尺寸）→ 同文件：重跑不重复计费。"""
    resp = _FakeResponse(payload={"data": [{"b64_json": base64.b64encode(_PNG).decode()}]})
    p = _provider(_FakeClient(resp))
    a = await p.generate("猫。", {"out_dir": str(tmp_path)})
    b = await p.generate("猫。", {"out_dir": str(tmp_path)})
    assert a == b
    # 换尺寸 → 换文件（尺寸进缓存键）
    c = await p.generate("猫。", {"out_dir": str(tmp_path), "size": "720x1440"})
    assert c != a


def test_satisfies_image_protocol() -> None:
    assert isinstance(
        OpenAICompatibleImageProvider(api_key="k", base_url="https://x/v1"),
        ImageProvider,
    )


# ----- resolve 接线：厂商预置 -----

_ENV_KEYS = (
    "STEPWORK_IMAGE_PROVIDER",
    "STEPWORK_IMAGE_API_KEY",
    "STEPWORK_IMAGE_BASE_URL",
    "STEPWORK_IMAGE_MODEL",
    "STEPWORK_IMAGE_SIZE",
    "STEPWORK_IMAGE_SIZE_KEY",
)


@pytest.fixture(autouse=True)
def _clean_env() -> Any:
    saved = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.mark.parametrize("kind", sorted(IMAGE_PRESETS))
def test_preset_needs_only_api_key(kind: str) -> None:
    """预置厂商：只给密钥就能用（base_url / model / size 都带默认值）。"""
    os.environ["STEPWORK_IMAGE_PROVIDER"] = kind
    assert resolve_image() is None  # 缺密钥 → None，绝不打空密钥到线上
    os.environ["STEPWORK_IMAGE_API_KEY"] = "sk-test"
    p = resolve_image()
    assert isinstance(p, OpenAICompatibleImageProvider)
    preset = IMAGE_PRESETS[kind]
    assert p.base_url == preset.base_url
    assert p.model == preset.model
    assert p.size == preset.size
    assert p.size_key == preset.size_key


def test_preset_values_can_be_overridden() -> None:
    os.environ["STEPWORK_IMAGE_PROVIDER"] = "siliconflow"
    os.environ["STEPWORK_IMAGE_API_KEY"] = "sk-test"
    os.environ["STEPWORK_IMAGE_MODEL"] = "black-forest-labs/FLUX.1-schnell"
    os.environ["STEPWORK_IMAGE_SIZE"] = "720x1440"
    os.environ["STEPWORK_IMAGE_SIZE_KEY"] = "size"
    p = resolve_image()
    assert isinstance(p, OpenAICompatibleImageProvider)
    assert p.model == "black-forest-labs/FLUX.1-schnell"
    assert p.size == "720x1440"
    assert p.size_key == "size"


def test_generic_openai_compatible_needs_base_url() -> None:
    """无预置的网关：base_url 必须自己给，否则无从发请求。"""
    os.environ["STEPWORK_IMAGE_PROVIDER"] = "openai-compatible"
    os.environ["STEPWORK_IMAGE_API_KEY"] = "sk-test"
    assert resolve_image() is None
    os.environ["STEPWORK_IMAGE_BASE_URL"] = "https://gw.example.com/v1"
    p = resolve_image()
    assert isinstance(p, OpenAICompatibleImageProvider)
    assert p.base_url == "https://gw.example.com/v1"


def test_unknown_or_dead_vendor_is_none() -> None:
    """未接的厂商（含已无生图模型的 stepfun）一律 None，不静默回落占位图。"""
    os.environ["STEPWORK_IMAGE_API_KEY"] = "sk-test"
    for kind in ("stepfun", "wanxiang", "qwen-image", "", "  "):
        os.environ["STEPWORK_IMAGE_PROVIDER"] = kind
        assert resolve_image() is None, kind
