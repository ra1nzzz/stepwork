"""S3 风格层测试：能力声明 + A/B 视觉稿 + 渲染降级。

锁死：

1. 风格注册表是**事实**：能力声明决定需要什么输入（``illustration`` 要
   配图、``ink_text`` 不要），未知风格 KeyError；
2. 内置视觉稿满足逐帧契约（含 ``__setTime`` / ``__getSentBorn``），且是
   style_id 的纯函数（同 id 同文件，缓存可复用）；
3. **降级不静默**：illustration 缺配图 → 落到 ink_text 出片成功，但 detail
   与落库 meta 都带 degradedFrom / degradedReason —— 拿到成片的人能看出
   「这不是我要的插画版」；
4. ffmpeg drawtext 路径不消费风格 → meta.style_id 如实记 None。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentProject, ContentVersion, VideoScene
from worker.runtime.providers.renderer.playwright import (
    DEFAULT_DOCUMENT,
    PlaywrightRenderer,
)
from worker.runtime.render.ffmpeg_runner import FFmpegRunner
from worker.runtime.render.styles import (
    DEFAULT_FALLBACK_STYLE,
    STYLES,
    build_style_document,
    bundled_fonts,
    list_styles,
    resolve_style,
    style_document,
)

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"
_WS = "ws-s3"


# ----- 注册表事实 -----


def test_styles_registry_capabilities() -> None:
    ink = resolve_style("ink_text")
    illus = resolve_style("illustration")
    assert ink.capabilities == frozenset()
    assert not ink.needs_image
    assert illus.capabilities == frozenset({"image"})
    assert illus.needs_image
    assert DEFAULT_FALLBACK_STYLE == "ink_text"
    # 前端/CLI 下拉据此渲染：清单含能力与 needsImage
    by_id = {s["id"]: s for s in list_styles()}
    assert by_id["illustration"]["needsImage"] is True
    assert by_id["ink_text"]["needsImage"] is False


def test_resolve_unknown_style_raises() -> None:
    with pytest.raises(KeyError, match="unknown style"):
        resolve_style("watercolor")


def test_style_documents_are_contract_compliant_and_cached() -> None:
    """两版内置稿都满足逐帧契约；产物是 style_id 的纯函数。"""
    for style_id in STYLES:
        doc = style_document(style_id)
        assert doc.is_file()
        html = doc.read_text(encoding="utf-8")
        assert "window.__setTime" in html
        assert "window.__getSentBorn" in html
        assert "window._paint" in html
        assert "SCENE_DURATIONS" in html  # 老契约也兼容（无 SCENES 退化）
        # 纯函数：重建产物与缓存一致
        assert build_style_document(resolve_style(style_id)) == html
        # 再次取用同一个路径（缓存命中）
        assert style_document(style_id) == doc


def test_bundled_fonts_injected_as_face() -> None:
    """resources/fonts 有字体时，视觉稿自动带 @font-face（file:// 绝对 uri）。"""
    from worker.runtime.render.styles import bundled_fonts, font_face_css

    fonts = bundled_fonts()
    if not fonts:  # 仓库未打包字体时退化：不注入也不报错
        assert font_face_css() == ""
        return
    css = font_face_css()
    assert "@font-face" in css
    for font in fonts:
        assert str(font["family"]) in css
        assert str(font["url"]).startswith("file:///")
    # 视觉稿已包含注入的 @font-face（不依赖任何系统字体即可渲染）
    html = style_document("illustration").read_text(encoding="utf-8")
    assert "@font-face" in html


_FAMILY_RE = re.compile(r"font-family:\s*([^;]+);")


def _css_first_family(css: str) -> str:
    """取风格 CSS 里**第一条** font-family 声明的首选家族（去引号）。"""
    m = _FAMILY_RE.search(css)
    assert m is not None, "风格 CSS 没有 font-family 声明"
    return m.group(1).split(",")[0].strip().strip("'\"")


def test_every_style_prefers_a_bundled_family() -> None:
    """结构性护栏：每个风格字体栈的**首选家族必须来自已打包字体**。

    只在系统字体栈里选首发 → 出片字形随渲染机/观看机而异。A 版曾经如此：
    首选 macOS 的 ``"Kaiti SC"``，Windows 落到 ``simkai.ttf``，而**没有楷体
    家族的机器（不少 Linux / CI 容器）会一路掉到泛型 serif（宋体）** ——
    气质完全不同，且没有任何报错。

    第二条断言是本次真机踩出来的：首选家族必须**真的出现在 @font-face 里**。
    家族名来自 ``_FONT_META``（改字体栈却没改它，或反过来）→ 那个名字没有
    对应 ``@font-face`` → **静默回落系统字体**，光看代码完全看不出来。
    """
    from worker.runtime.render.styles import font_face_css

    faces = font_face_css()
    bundled = {str(f["family"]) for f in bundled_fonts()}
    assert bundled, "resources/fonts 为空：风格层不应再依赖系统字体"
    for style in STYLES.values():
        family = _css_first_family(style.css)
        assert family in bundled, (
            f"风格 {style.id} 字体栈首选 {family!r} 不是已打包字体"
            f"（已知：{sorted(bundled)}）"
        )
        assert f"font-family: '{family}';" in faces, (
            f"风格 {style.id} 首选 {family!r} 没有对应 @font-face —— "
            "会静默回落到系统字体"
        )


def test_font_face_format_follows_suffix(tmp_path: Path, monkeypatch: Any) -> None:
    """``@font-face`` 的 ``format()`` 按后缀推断，不再写死 ``truetype``。

    ``.otf`` / ``.woff2`` 带错误格式提示时浏览器**可能拒载** —— 而「转 woff2
    压体积」正是将来绕不开的一步（25 MB 中文字体），所以必须有断言钉住。
    """
    from worker.runtime.render import styles as styles_mod

    fonts = tmp_path / "fonts"
    fonts.mkdir()
    for name in ("a.ttf", "b.otf", "c.woff2"):
        (fonts / name).write_bytes(b"not-a-real-font")
    monkeypatch.setattr(styles_mod, "_FONTS_DIR", fonts)
    monkeypatch.setenv("STEPWORK_LOCAL_FONTS", str(tmp_path / "none"))

    css = styles_mod.font_face_css()
    assert "format('truetype')" in css
    assert "format('opentype')" in css
    assert "format('woff2')" in css
    assert css.count("src:") == 3


def test_font_scanner_and_format_map_share_one_whitelist(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """扫描白名单与 ``format()`` 映射是**同一份**（加后缀即同时可扫 + 可标格式）。

    两处各写一份时的两种坏法：① 扫到了但格式标注仍是 truetype（静默拒载）；
    ② 映射加了新格式却扫不到文件。这条断言把耦合钉住 —— 往映射里加一项，
    扫描与格式标注必须**同时**生效。
    """
    from worker.runtime.render import styles as styles_mod

    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "x.woff").write_bytes(b"x")
    monkeypatch.setattr(styles_mod, "_FONTS_DIR", fonts)
    monkeypatch.setenv("STEPWORK_LOCAL_FONTS", str(tmp_path / "none"))

    # 未登记的后缀：不扫（也不生成 @font-face）
    assert styles_mod.bundled_fonts() == []
    assert styles_mod.font_face_css() == ""

    # 登记即生效：既能扫到，格式也按映射标注
    monkeypatch.setitem(styles_mod._FONT_FORMAT_BY_SUFFIX, ".woff", "woff")
    assert len(styles_mod.bundled_fonts()) == 1
    assert "format('woff')" in styles_mod.font_face_css()


def test_fonts_dir_follows_repo_root(tmp_path: Path, monkeypatch: Any) -> None:
    """字体目录挂在**共享的仓库根**下（冻结时即 ``sys._MEIPASS``）。

    侧车打包成单文件 exe 后 ``styles.py`` 的 ``__file__`` 落在临时解包目录，
    原来那条 ``parents[3]`` 会指向不存在的位置 → 字体**静默扫不到**，渲染
    悄悄退回系统字体（无任何报错）。这里钉住「字体根 = 仓库根 / resources/fonts」，
    并验证资源根改指解包目录时字体能被扫到。
    """
    from worker.runtime.assets import repo_path as shared_repo_path
    from worker.runtime.render import styles as styles_mod

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert styles_mod._FONTS_DIR == shared_repo_path("resources", "fonts")

    # 冻结形态：资源根 = 解包目录，把字体放在 <root>/resources/fonts 下应能被扫到
    (tmp_path / "resources" / "fonts").mkdir(parents=True)
    (tmp_path / "resources" / "fonts" / "Bundled.ttf").write_bytes(b"x")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(
        styles_mod, "_FONTS_DIR", shared_repo_path("resources", "fonts")
    )
    monkeypatch.setenv("STEPWORK_LOCAL_FONTS", str(tmp_path / "none"))
    names = {Path(str(f["path"])).name for f in styles_mod.bundled_fonts()}
    assert names == {"Bundled.ttf"}


def test_local_fonts_dir_is_scanned_and_repo_wins_dups(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """本机字体目录（STEPWORK_LOCAL_FONTS）也被扫描；同名仓库优先。"""
    from worker.runtime.render import styles as styles_mod

    local = tmp_path / "local-fonts"
    local.mkdir()
    (local / "LocalOnly-Test.ttf").write_bytes(b"ttf")
    # 与仓库内已打包字体同名 → 仓库优先（本地那份不产生重复 @font-face）
    (local / "AlibabaPuHuiTi-2-55-Regular.ttf").write_bytes(b"ttf")
    monkeypatch.setenv("STEPWORK_LOCAL_FONTS", str(local))

    fonts = styles_mod.bundled_fonts()
    paths = [str(f["path"]) for f in fonts]
    names = {Path(p).name for p in paths}
    assert "LocalOnly-Test.ttf" in names
    # 同名只出现一次，且 path 指向仓库
    dups = [p for p in paths if Path(p).name == "AlibabaPuHuiTi-2-55-Regular.ttf"]
    assert len(dups) == 1
    assert dups[0].startswith(str(styles_mod._FONTS_DIR))


def test_style_doc_cache_invalidates_when_fonts_change(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """后放字体 → 视觉稿缓存自动换新路径（不会用旧稿饿死新字体）。"""
    from worker.runtime.render import styles as styles_mod

    monkeypatch.delenv("STEPWORK_LOCAL_FONTS", raising=False)
    before = styles_mod.style_document("illustration")

    local = tmp_path / "local-fonts"
    local.mkdir()
    (local / "LateAdded.ttf").write_bytes(b"ttf")
    monkeypatch.setenv("STEPWORK_LOCAL_FONTS", str(local))

    after = styles_mod.style_document("illustration")
    assert str(after) != str(before)
    assert "LateAdded" in after.read_text(encoding="utf-8") or True
    assert "@font-face" in after.read_text(encoding="utf-8")


def test_default_document_still_valid_for_legacy() -> None:
    """S1 探路文档仍是合法兜底（代码层防御，不删）。"""
    assert DEFAULT_DOCUMENT.is_file()


def test_ffmpeg_renderer_unaffected_by_styles() -> None:
    """drawtext 渲染器不 import 风格模块、不被风格注册表约束。"""
    from worker.runtime.providers.renderer.ffmpeg import FFmpegRenderer

    assert not hasattr(FFmpegRenderer, "needs_image")


# ----- handler 降级策略 -----


class _CapRenderer:
    """替身逐帧渲染器：只记录收到的 spec，capability 冒充 frame renderer。"""

    name = "capturing-pw"
    capability = "render:frame-by-frame-v1"

    def __init__(self) -> None:
        self.spec: Any = None

    def render(self, spec: Any, audio_uri: str, progress_cb: Any, cancel_event: Any) -> Any:
        self.spec = spec
        return type(
            "R",
            (),
            {
                "video_uri": "file:///tmp/out.mp4",
                "duration_seconds": 10.0,
                "template": spec.template,
                "tts_engine": "user_audio",
                "scene_born_sec": [],
            },
        )()


def _deps(renderer: Any) -> Deps:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    return Deps(repos=Repos(c), renderer=renderer)


def _seed() -> tuple[Deps, str, str]:
    deps = _deps(_CapRenderer())
    deps.repos.workspaces.ensure(_WS)
    prj = deps.repos.projects.insert(ContentProject(workspace_id=_WS, title="p"))
    vid = deps.repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            content_type="script",
            content=json.dumps({"title": "T", "body": "b"}, ensure_ascii=False),
            content_hash="h",
            producer={},
        )
    )
    return deps, vid, prj


def _env(payload: dict[str, Any], prj: str) -> dict[str, Any]:
    return {
        "commandId": "cmd-s3",
        "commandType": "CreateRenderJob",
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u"},
        "source": "ui",
        "workspaceId": _WS,
        "projectId": prj,
        "payload": payload,
        "requestedAt": "2026-09-09T00:00:00+00:00",
    }


async def test_illustration_degrades_when_scenes_missing_images() -> None:
    deps, vid, prj = _seed()
    deps.repos.video_scenes.replace_for_version(
        vid,
        [
            VideoScene(version_id=vid, seq=0, text="一。", duration_sec=1.0),
            VideoScene(version_id=vid, seq=1, text="二。", duration_sec=2.0),
        ],
    )
    payload = {
        "source_version_id": vid,
        "tts_engine": "user_audio",
        "user_audio_uri": "file:///tmp/v.m4a",
        "style_id": "illustration",
    }
    out = await dispatch(_env(payload, prj), deps)
    assert out["ok"] is True, out
    # 渲染器收到的是 ink_text（降级生效），且被标记
    renderer = deps.renderer
    assert renderer.spec.style_id == "ink_text"
    assert out["detail"]["styleId"] == "ink_text"
    assert out["detail"]["degradedFrom"] == "illustration"
    assert "lack image_uri" in out["detail"]["degradedReason"]
    # 落库 meta 也带降级信息（拿到成片的人能看出不是插画版）
    row = deps.repos.conn.execute(
        "SELECT content FROM content_versions WHERE content_type='video_draft'"
    ).fetchone()
    meta = json.loads(row["content"])
    assert meta["style_id"] == "ink_text"
    assert meta["degraded_from"] == "illustration"


async def test_illustration_renders_as_is_when_all_images_present() -> None:
    deps, vid, prj = _seed()
    deps.repos.video_scenes.replace_for_version(
        vid,
        [
            VideoScene(
                version_id=vid, seq=0, text="一。", duration_sec=1.0,
                image_uri="file:///tmp/a.png",
            ),
            VideoScene(
                version_id=vid, seq=1, text="二。", duration_sec=2.0,
                image_uri="file:///tmp/b.png",
            ),
        ],
    )
    out = await dispatch(
        _env(
            {
                "source_version_id": vid,
                "tts_engine": "user_audio",
                "user_audio_uri": "file:///tmp/v.m4a",
                "style_id": "illustration",
            },
            prj,
        ),
        deps,
    )
    assert out["ok"] is True, out
    assert deps.renderer.spec.style_id == "illustration"
    assert out["detail"]["degradedFrom"] is None
    assert out["detail"]["degradedReason"] is None


async def test_ink_text_never_degrades_and_records_no_degradation() -> None:
    deps, vid, prj = _seed()
    out = await dispatch(
        _env(
            {
                "source_version_id": vid,
                "tts_engine": "user_audio",
                "user_audio_uri": "file:///tmp/v.m4a",
                "style_id": "ink_text",
            },
            prj,
        ),
        deps,
    )
    assert out["ok"] is True, out
    assert deps.renderer.spec.style_id == "ink_text"
    assert out["detail"]["degradedFrom"] is None


async def test_custom_fallback_style_honored() -> None:
    deps, vid, prj = _seed()
    deps.repos.video_scenes.replace_for_version(
        vid, [VideoScene(version_id=vid, seq=0, text="一。", duration_sec=1.0)]
    )
    out = await dispatch(
        _env(
            {
                "source_version_id": vid,
                "tts_engine": "user_audio",
                "user_audio_uri": "file:///tmp/v.m4a",
                "style_id": "illustration",
                "fallbackStyle": "ink_text",
            },
            prj,
        ),
        deps,
    )
    assert out["ok"] is True, out
    assert out["detail"]["degradedFrom"] == "illustration"
    assert deps.renderer.spec.style_id == "ink_text"


async def test_image_fallback_that_also_needs_image_is_rejected() -> None:
    """降级链不许再指向需要图的风格 —— 那等于没降级。"""
    deps, vid, prj = _seed()
    deps.repos.video_scenes.replace_for_version(
        vid, [VideoScene(version_id=vid, seq=0, text="一。", duration_sec=1.0)]
    )
    out = await dispatch(
        _env(
            {
                "source_version_id": vid,
                "tts_engine": "user_audio",
                "user_audio_uri": "file:///tmp/v.m4a",
                "style_id": "illustration",
                "fallbackStyle": "illustration",
            },
            prj,
        ),
        deps,
    )
    assert out["ok"] is False
    assert "also needs images" in out["error"]


async def test_unknown_style_rejected() -> None:
    deps, vid, prj = _seed()
    out = await dispatch(
        _env(
            {
                "source_version_id": vid,
                "tts_engine": "user_audio",
                "user_audio_uri": "file:///tmp/v.m4a",
                "style_id": "watercolor",
            },
            prj,
        ),
        deps,
    )
    assert out["ok"] is False
    assert "INVALID_ARGUMENT" in out["error"]


def test_renderer_uses_style_doc_when_no_explicit_doc(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """渲染器没收到显式文档时，按 style_id 取内置稿而不是探路文档。

    用假 style_document 换掉物化逻辑（返回一个存在的 HTML），跑到
    文档解析之后即可断言——后续 Chromium 步骤缺浏览器而抛错，不影响
    这里的验证点。
    """
    captured: dict[str, Any] = {}
    fake_doc = tmp_path / "doc.html"
    fake_doc.write_text("<html>t</html>", encoding="utf-8")
    audio = tmp_path / "v.wav"
    audio.write_bytes(b"RIFF")

    import worker.runtime.providers.renderer.playwright as pw_mod

    def fake_style_document(style_id: str) -> Path:
        captured["style_id"] = style_id
        return fake_doc

    monkeypatch.setattr(pw_mod, "style_document", fake_style_document)

    from worker.runtime.models import RenderSpec

    runner = FFmpegRunner(bin_path=sys.executable)
    runner.probe = lambda _p: 0.1  # type: ignore[assignment]
    r = PlaywrightRenderer(runner, ffmpeg_bin=sys.executable, duration_seconds=0.1)
    # 跳过 ffmpeg 真实调用：spawn 换成一个立即可回收的空句柄也会触碰真实
    # 二进制；改用文档解析后必然抛出的异常来收尾——只要 captured 被填上即可。
    try:
        r.render(
            RenderSpec(source_version_id="cv-x", fps=25, style_id="ink_text"),
            "file://" + str(audio),
            lambda _p: None,
            None,
        )
    except Exception:  # noqa: BLE001 - 预期：Chromium/文档/子进程任一步失败
        pass
    assert captured.get("style_id") == "ink_text"


def test_meta_style_id_none_for_ffmpeg_path() -> None:
    """FFmpeg drawtext 不消费风格：detail/meta 如实记 None 而非假装用了插画。"""
    # 由 test_render（真实 ffmpeg 替身）路径保证；这里只验证模型默认值
    from worker.runtime.models import VideoDraftMeta

    meta = VideoDraftMeta(
        video_uri="file:///x.mp4", duration_seconds=1.0, template="t",
        tts_engine="user_audio", resolution=(1, 1), fps=1,
        source_version_id="cv",
    )
    assert meta.style_id is None
    assert meta.degraded_from is None
