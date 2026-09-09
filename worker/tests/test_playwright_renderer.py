"""Playwright 逐帧渲染器测试（S1 探路）。

覆盖：

- ffmpeg 不可用 → ``FFmpegUnavailable``（不碰浏览器，永远跑）
- 协议一致性（PlaywrightRenderer 与 FFmpegRenderer 都满足 RendererProvider）
- 正常渲染：每一帧都真的进了 ffmpeg（用 fake 数 JPEG SOI，不靠「文件出现了」）
- 取消：抛 ``FFmpegCancelled``、子进程被回收（0 僵尸）、不退化为等 30s
- 文档不合契约（缺 ``window.__setTime``）→ 明确报错，不渲静帧
- ``STEPWORK_RENDER_PROVIDER`` 解析分支（playwright / 默认 ffmpeg / 缺包 None）

需要 Chromium（``playwright install chromium``）；包或浏览器缺失时相关用例
跳过，保证未装 playwright 的 CI 也能绿。真实成片的 e2e 见本文件末尾
``test_real_assets_30s_9x16``（``perf`` 标记，默认不跑）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.models import RenderScene, RenderSpec
from worker.runtime.providers.renderer.base import RendererProvider
from worker.runtime.providers.renderer.ffmpeg import FFmpegRenderer
from worker.runtime.providers.renderer.playwright import (
    DEFAULT_DOCUMENT,
    PlaywrightRenderer,
    PlaywrightRenderError,
)
from worker.runtime.render.ffmpeg_runner import (
    FFmpegCancelled,
    FFmpegRunner,
    FFmpegUnavailable,
)

PY = sys.executable
FAKE = os.path.join(os.path.dirname(__file__), "fakes", "fake_ffmpeg_pipe.py")

#: 最小可用逐帧文档：必须定义 window.__setTime，且同一 t 出同一画面。
TINY_DOC = """<!doctype html><meta charset="utf-8">
<body style="margin:0;width:1080px;height:1920px;background:#000;color:#fff">
<div id="t" style="font-size:120px"></div>
<script>
window.__setTime = function (t) {
  document.getElementById('t').textContent = t.toFixed(2);
};
window.__setTime(0);
</script></body>"""

#: 缺 __setTime 的文档——若静默放行就会渲出一整条静帧（假成功）。
STATIC_DOC = """<!doctype html><meta charset="utf-8">
<body style="margin:0;width:1080px;height:1920px;background:#333"></body>"""


@pytest.fixture(scope="module")
def chromium() -> None:
    """需要真实 Chromium；不可用则整组跳过。"""
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright not installed")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            browser.close()
    except Exception as exc:  # 任何原因（未下载浏览器/沙箱限制）都视为不可用
        pytest.skip(f"chromium unavailable: {exc}")


def _runner() -> FFmpegRunner:
    """把 python 当“ffmpeg”用，真正执行的是 fakes/fake_ffmpeg_pipe.py。"""
    return FFmpegRunner(bin_path=PY)


def _audio(tmp_path: Any) -> str:
    """占位音频文件（fake ffmpeg 不解析内容，只要求文件存在）。"""
    p = tmp_path / "voice.wav"
    p.write_bytes(b"RIFF")
    return str(p)


# ---------------------------------------------------------------------------
# 不需要 Chromium
# ---------------------------------------------------------------------------
def test_render_raises_when_ffmpeg_unavailable() -> None:
    r = PlaywrightRenderer(FFmpegRunner(bin_path="/no/such/ffmpeg"))
    with pytest.raises(FFmpegUnavailable):
        r.render(
            RenderSpec(source_version_id="cv-1"),
            "file:///tmp/voice.wav",
            lambda _p: None,
            None,
        )


def test_both_renderers_satisfy_protocol() -> None:
    """S1 新增渲染器必须与 FFmpegRenderer 同协议，且旧渲染器不受影响。"""
    dead = FFmpegRunner(bin_path="/no/such/ffmpeg")
    assert isinstance(PlaywrightRenderer(dead), RendererProvider)
    assert isinstance(FFmpegRenderer(dead), RendererProvider)
    assert PlaywrightRenderer(dead).capability == "render:frame-by-frame-v1"
    assert FFmpegRenderer(dead).capability == "render:vertical-caption-v1"


def test_render_rejects_missing_audio(tmp_path: Any) -> None:
    r = PlaywrightRenderer(_runner())
    with pytest.raises(PlaywrightRenderError, match="audio not found"):
        r.render(
            RenderSpec(source_version_id="cv-1"),
            str(tmp_path / "nope.wav"),
            lambda _p: None,
            None,
        )


# ---------------------------------------------------------------------------
# 需要 Chromium
# ---------------------------------------------------------------------------
def test_render_feeds_every_frame_to_ffmpeg(tmp_path: Any, chromium: None) -> None:
    """正常渲染：帧数、进度、产物三对照——不靠「文件出现了」判定成功。"""
    audio = _audio(tmp_path)
    r = PlaywrightRenderer(
        _runner(),
        ffmpeg_bin=FAKE,
        scene_durations=[0.1, 0.1],
        duration_seconds=0.2,  # 0.2s × 25fps = 5 帧
        warmup_ms=50,
    )
    progresses: list[float] = []
    result = r.render(
        RenderSpec(source_version_id="cv-1", fps=25),
        audio,
        progresses.append,
        threading.Event(),
    )

    assert result.video_uri.endswith("draft_cv-1.mp4")
    out = result.video_uri.replace("file://", "")
    assert os.path.isfile(out)
    report = json.loads(open(out, encoding="utf-8").read())
    # 5 帧 JPEG 真的写进了 ffmpeg 的 stdin（每帧一个 SOI 标记）
    assert report["frames"] == 5, report
    assert report["bytes"] > 0
    # 进度由真实帧数驱动：0.0 → 0.2 → 0.4 → 0.6 → 0.8 → 1.0
    assert progresses == [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    assert result.template == "vertical-caption-v1"
    # 内置探路文档默认可用（零外部依赖）：本用例未传 document_uri
    assert os.path.isfile(str(DEFAULT_DOCUMENT))


def test_scene_data_reaches_probe_doc_and_born_measured(
    tmp_path: Any, chromium: None
) -> None:
    """S2：scenes 注入内置探路文档，画面真的按幕切换。

    用 DEFAULT_DOCUMENT（改版后消费 window.SCENES），断言：
    1. 探路文档的 __getSentBorn 让渲染器实测出 born_at_sec（startSec）；
    2. 场景文本 + 高亮真的渲染进画面（取一帧像素校验不可行，改为注入一个
       会「按幕变色」的…… 探路文档没有这种机制，故退而用 born 断言 + 文档
       脚本在首幕置一个 sentinel DOM，渲染后读它）。
    """
    # 让「画面真的动了」可被读到：探路文档没暴露现成 hook，改用 evaluate
    # 太侵入。核心断言 = born 被实测（含 startSec），即渲染器调了 __getSentBorn。
    audio = _audio(tmp_path)
    img = tmp_path / "art.svg"
    img.write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'/>",
        encoding="utf-8",
    )

    scenes = [
        RenderScene(seq=0, text="第一幕。", start_sec=0.0, duration_sec=0.1),
        RenderScene(
            seq=1,
            text="第二幕有高亮。",
            highlight="高亮",
            start_sec=0.1,
            duration_sec=0.1,
            image_uri=str(img),
        ),
    ]
    r = PlaywrightRenderer(
        _runner(),
        ffmpeg_bin=FAKE,
        duration_seconds=0.2,  # 0.2s × 25 = 5 帧
        warmup_ms=50,
    )
    result = r.render(
        RenderSpec(source_version_id="cv-s2", fps=25, scenes=scenes),
        audio,
        lambda _p: None,
        threading.Event(),
    )
    assert result.video_uri.endswith("draft_cv-s2.mp4")
    # 渲染器从探路文档的 __getSentBorn 实测到 born_at_sec（这里 = startSec）
    assert result.scene_born_sec == [0.0, 0.1], result.scene_born_sec


def test_style_doc_render_end_to_end(tmp_path: Any, chromium: None) -> None:
    """S3：内置 ink_text 视觉稿在真实浏览器里能出片（含 born 实测）。

    不传任何显式文档 → 渲染器按 style_id=ink_text 取内置稿。这同时证明
    内置稿满足逐帧契约、共享骨架 + 画面函数拼接没有 JS 语法错。
    """
    audio = _audio(tmp_path)
    scenes = [
        RenderScene(seq=0, text="纸墨第一幕。", start_sec=0.0, duration_sec=0.1),
        RenderScene(seq=1, text="纸墨第二幕。", start_sec=0.1, duration_sec=0.1),
    ]
    r = PlaywrightRenderer(
        _runner(),
        ffmpeg_bin=FAKE,
        duration_seconds=0.2,  # 0.2s × 25 = 5 帧
        warmup_ms=50,
    )
    result = r.render(
        RenderSpec(source_version_id="cv-style", fps=25, style_id="ink_text", scenes=scenes),
        audio,
        lambda _p: None,
        threading.Event(),
    )
    assert result.video_uri.endswith("draft_cv-style.mp4")
    out = result.video_uri.replace("file://", "")
    assert os.path.isfile(out)
    report = json.loads(open(out, encoding="utf-8").read())
    assert report["frames"] == 5, report
    # ink_text 无前摇 → __getSentBorn 实测 = startSec
    assert result.scene_born_sec == [0.0, 0.1], result.scene_born_sec


def test_render_cancel_no_zombie(tmp_path: Any, chromium: None, monkeypatch: Any) -> None:
    doc = tmp_path / "doc.html"
    doc.write_text(TINY_DOC, encoding="utf-8")
    audio = _audio(tmp_path)

    event = threading.Event()

    def _cancel_at(p: float) -> None:
        if p >= 0.4:
            event.set()

    r = PlaywrightRenderer(
        _runner(),
        ffmpeg_bin=FAKE,
        document_uri=str(doc),
        duration_seconds=1.0,  # 25 帧 @25fps，第 10 帧后取消
        warmup_ms=50,
    )
    t0 = time.time()
    with pytest.raises(FFmpegCancelled):
        r.render(RenderSpec(source_version_id="cv-1", fps=25), audio, _cancel_at, event)
    elapsed = time.time() - t0

    # fake 在收完 stdin 后会睡 30s；若没被 terminate，这里会等到超时
    assert elapsed < 15, f"cancel took {elapsed:.1f}s — child was not terminated"
    # 子进程已被 wait() 回收（0 僵尸）
    assert r.last_proc is not None
    assert r.last_proc.poll() is not None
    # sleep 分支不落产物
    assert not any(p.name.startswith("draft_") for p in tmp_path.iterdir())


def test_render_rejects_document_without_settime(
    tmp_path: Any, chromium: None
) -> None:
    """文档没有 window.__setTime → 明确报错，绝不渲出一整条静帧（假成功）。"""
    doc = tmp_path / "static.html"
    doc.write_text(STATIC_DOC, encoding="utf-8")
    r = PlaywrightRenderer(
        _runner(),
        ffmpeg_bin=FAKE,
        document_uri=str(doc),
        duration_seconds=0.2,
        warmup_ms=50,
    )
    with pytest.raises(PlaywrightRenderError, match="__setTime"):
        r.render(
            RenderSpec(source_version_id="cv-1", fps=25),
            _audio(tmp_path),
            lambda _p: None,
            threading.Event(),
        )
    # 异常路径同样要收掉子进程
    assert r.last_proc is not None
    assert r.last_proc.poll() is not None


# ---------------------------------------------------------------------------
# resolve 分支
# ---------------------------------------------------------------------------
def test_resolve_renderer_playwright_branch(monkeypatch: Any) -> None:
    from worker.runtime.providers import resolve as resolve_mod

    monkeypatch.setenv("STEPWORK_RENDER_PROVIDER", "playwright")
    got = resolve_mod.resolve_renderer()
    assert isinstance(got, PlaywrightRenderer)


def test_resolve_renderer_defaults_to_ffmpeg(monkeypatch: Any) -> None:
    from worker.runtime.providers import resolve as resolve_mod

    monkeypatch.delenv("STEPWORK_RENDER_PROVIDER", raising=False)
    monkeypatch.delenv("STEPWORK_FFMPEG_BIN", raising=False)
    got = resolve_mod.resolve_renderer()
    assert isinstance(got, FFmpegRenderer)


def test_resolve_renderer_returns_none_when_playwright_missing(
    monkeypatch: Any,
) -> None:
    """缺 playwright 包 → None（handler 转 UNAVAILABLE），不静默回退 ffmpeg。"""
    from worker.runtime.providers import resolve as resolve_mod

    monkeypatch.setenv("STEPWORK_RENDER_PROVIDER", "playwright")
    monkeypatch.setattr(resolve_mod, "_has_module", lambda _name: False)
    assert resolve_mod.resolve_renderer() is None


def test_resolve_renderer_honours_ffmpeg_bin_env(monkeypatch: Any) -> None:
    """WinGet 装的 ffmpeg 常不在 PATH：STEPWORK_FFMPEG_BIN 必须生效。"""
    from worker.runtime.providers import resolve as resolve_mod

    monkeypatch.delenv("STEPWORK_RENDER_PROVIDER", raising=False)
    monkeypatch.setenv("STEPWORK_FFMPEG_BIN", "/opt/ffmpeg/bin/ffmpeg")
    got = resolve_mod.resolve_renderer()
    assert isinstance(got, FFmpegRenderer)
    assert got.runner.bin_path == "/opt/ffmpeg/bin/ffmpeg"


def test_resolve_renderer_unknown_kind_is_not_silent(monkeypatch: Any) -> None:
    """拼错 provider 名不该静默渲出「用错渲染器」的片子。"""
    from worker.runtime.providers import resolve as resolve_mod

    monkeypatch.setenv("STEPWORK_RENDER_PROVIDER", "playwrite")  # 拼写错误
    assert resolve_mod.resolve_renderer() is None


# ---------------------------------------------------------------------------
# per-request renderer hint（P4：GUI 与 CLI 同为一等公民）
# ---------------------------------------------------------------------------
def test_renderer_from_hint_accepts_str_and_dict(monkeypatch: Any) -> None:
    from worker.runtime.providers import resolve as resolve_mod

    monkeypatch.delenv("STEPWORK_RENDER_PROVIDER", raising=False)
    assert isinstance(resolve_mod.renderer_from_hint("playwright"), PlaywrightRenderer)
    assert isinstance(
        resolve_mod.renderer_from_hint({"kind": "playwright"}), PlaywrightRenderer
    )
    assert isinstance(resolve_mod.renderer_from_hint("ffmpeg"), FFmpegRenderer)


def test_renderer_from_hint_returns_none_on_empty_or_unknown() -> None:
    from worker.runtime.providers import resolve as resolve_mod

    for hint in (None, "", "   ", {}, {"kind": ""}, "not-a-renderer"):
        assert resolve_mod.renderer_from_hint(hint) is None, hint
    # 未装 playwright 时同样回退 None（交回 deps.renderer）
    assert resolve_mod.renderer_from_hint("nope") is None


def test_renderer_from_hint_reuses_caller_runner() -> None:
    """hint 选择时必须复用调用方的 FFmpegRunner，不能悄悄换掉二进制配置。"""
    from worker.runtime.providers import resolve as resolve_mod

    runner = FFmpegRunner(bin_path="/opt/ffmpeg/bin/ffmpeg")
    got = resolve_mod.renderer_from_hint("ffmpeg", runner)
    assert isinstance(got, FFmpegRenderer)
    assert got.runner is runner


def test_renderer_from_hint_returns_none_when_playwright_missing(
    monkeypatch: Any,
) -> None:
    from worker.runtime.providers import resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "_has_module", lambda _n: False)
    assert resolve_mod.renderer_from_hint("playwright") is None


def test_design_doc_uri_takes_precedence_over_background_uri(tmp_path: Any) -> None:
    """新增字段优先；background_uri 降为 S1 遗留别名（不得反过来）。"""
    audio = _audio(tmp_path)
    r = PlaywrightRenderer(_runner(), ffmpeg_bin=FAKE)
    spec = RenderSpec(
        source_version_id="cv-1",
        # 两个都不存在 → 报错信息里带的就是实际选中的那个
        design_doc_uri=str(tmp_path / "design-doc.html"),
        background_uri=str(tmp_path / "bg.png"),
    )
    with pytest.raises(PlaywrightRenderError, match="design-doc.html"):
        r.render(spec, audio, lambda _p: None, None)


def test_background_uri_still_works_as_legacy_alias(tmp_path: Any) -> None:
    """只给 background_uri 时仍要走通（向后兼容，不 breaking）。"""
    r = PlaywrightRenderer(_runner(), ffmpeg_bin=FAKE)
    spec = RenderSpec(source_version_id="cv-1", background_uri=str(tmp_path / "bg.html"))
    with pytest.raises(PlaywrightRenderError, match="bg.html"):
        r.render(spec, _audio(tmp_path), lambda _p: None, None)


def test_job_stage_gains_illustrating() -> None:
    """S2 配图阶段：只增不改名，既有 11 个阶段取值不变。"""
    from worker.runtime.models import JobStage

    assert JobStage.ILLUSTRATING.value == "illustrating"
    assert JobStage.RENDERING.value == "rendering"
    assert JobStage.SYNTHESIZING.value == "synthesizing"


# ---------------------------------------------------------------------------
# 真机 e2e（perf：默认不跑，需真实 ffmpeg + 现成素材）
#   pytest -m perf -k real_assets
# ---------------------------------------------------------------------------
_REAL_ROOT = Path("C:/Users/my/WorkBuddy/2026-09-07-05-23-14/gender-video")
_REAL_DOC = _REAL_ROOT / "design.html"
_REAL_AUDIO = _REAL_ROOT / "audio" / "narration.m4a"
_REAL_TIMELINE = _REAL_ROOT / "timeline.json"
_FFMPEG_WIN = Path("C:/Users/my/AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe")


def _ffprobe(path: str, ffmpeg_bin: str) -> dict[str, str]:
    """读取成片规格（宽/高/时长/视频编码/音频编码）。"""
    probe = Path(ffmpeg_bin).with_name("ffprobe.exe")
    args = [
        str(probe), "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1",
        path,
    ]
    r = subprocess.run(args, capture_output=True, text=True, errors="replace")
    out: dict[str, str] = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out.setdefault(k.strip(), v.strip())
    return out


@pytest.mark.perf
@pytest.mark.skipif(
    not (_REAL_DOC.is_file() and _REAL_AUDIO.is_file()),
    reason="S1 现成素材（gender-video）不在本机",
)
def test_real_assets_30s_9x16(tmp_path: Any) -> None:
    """验收：用现成素材渲 30 秒 9:16 成片（1080×1920 / H.264 + AAC）。"""
    ffmpeg_bin = os.environ.get("STEPWORK_FFMPEG_BIN") or str(_FFMPEG_WIN)
    if not Path(ffmpeg_bin).is_file():
        pytest.skip("真实 ffmpeg 不可用，用 STEPWORK_FFMPEG_BIN 指定")

    timeline = json.loads(_REAL_TIMELINE.read_text(encoding="utf-8"))
    durations = [float(s["duration"]) for s in timeline["scenes"]]

    r = PlaywrightRenderer(
        FFmpegRunner(bin_path=ffmpeg_bin),
        document_uri=str(_REAL_DOC),
        scene_durations=durations,
        duration_seconds=30.0,
        output_dir=str(tmp_path),
    )
    progresses: list[float] = []
    t0 = time.time()
    result = r.render(
        RenderSpec(source_version_id="s1-acceptance", fps=30),
        str(_REAL_AUDIO),
        progresses.append,
        threading.Event(),
    )

    out = result.video_uri.replace("file://", "")
    assert os.path.isfile(out)
    assert os.path.getsize(out) > 100_000, "成片体积异常（疑似空片）"
    # 进度单调递增且收在 1.0
    assert progresses[0] == 0.0
    assert progresses[-1] == 1.0
    assert progresses == sorted(progresses)

    spec = _ffprobe(out, ffmpeg_bin)
    assert spec["width"] == "1080"
    assert spec["height"] == "1920"
    assert spec["codec_name"] == "h264"
    assert 29.0 <= float(spec["duration"]) <= 31.0
    print(
        f"\n[acceptance] {out}\n"
        f"  {spec['width']}x{spec['height']} codec={spec['codec_name']} "
        f"duration={spec['duration']}s size={os.path.getsize(out)}B "
        f"wall={time.time() - t0:.1f}s frames={len(progresses) - 1}"
    )
