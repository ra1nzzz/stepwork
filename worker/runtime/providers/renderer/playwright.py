"""Playwright 逐帧渲染器（S1 探路）。

实现 :class:`~worker.runtime.providers.renderer.base.RendererProvider` 协议，
与 :class:`~worker.runtime.providers.renderer.ffmpeg.FFmpegRenderer` 平级——
后者是「纯色背景 + 一行 drawtext」的极简路径，本类是「HTML 视觉稿逐帧动画」
路径。**默认仍是 ffmpeg**；切本类需显式 ``STEPWORK_RENDER_PROVIDER=playwright``。

来源：本 workspace 已验证的 ``gender-video/scripts/render.py``（已出 3 条成片）。
三件必须照搬的事（踩过的坑都在这里）：

1. **管道直连**：Chromium 截图 → 直写 ffmpeg ``stdin``（``-f image2pipe``），
   **绝不落地中间帧**。1080×1920 逐帧落盘会瞬间堆出 GB 级临时文件并拖垮 IO。
2. **先音频、后画面**：总帧数 = 音频实测时长 × fps。画面被音频时长驱动，
   而不是反过来（对应 huashu-design「失败模式 #1 = 带配音的 PPT」）。
3. **时间是纯函数**：文档必须暴露 ``window.__setTime(t)``，且同一 ``t``
   必须出同一画面——否则抽帧目检与断点重渲都不可复现。

契约（S1 定死，S3 风格层沿用）：

- 渲染文档：``spec.design_doc_uri`` → ``spec.background_uri``（S1 遗留别名）
  → 构造参数 ``document_uri`` → 内置零依赖探路文档 :data:`DEFAULT_DOCUMENT`。
- 幕时长：由 ``scene_durations`` 注入为 ``window.SCENE_DURATIONS``。
- 进度：**由已写帧数驱动**（``i / nframes``）。ffmpeg 从管道读输入时无法预知
  总时长，stderr 解析不出 ``Duration:``，所以进度只能来自帧计数。
- 取消：复用 :class:`~worker.runtime.render.ffmpeg_runner.FFmpegRunner` 的
  ``terminate → kill → wait`` 语义（:meth:`FFmpegRunner.supervise`）。
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from worker.runtime.models import RenderResult, RenderScene, RenderSpec
from worker.runtime.render.ffmpeg_runner import (
    FFmpegFailed,
    FFmpegRunner,
    FFmpegUnavailable,
)
from worker.runtime.render.styles import style_document

#: 内置 S1 探路文档（零外部依赖：无字体 / 无图片 / 无网络）。
#: 未显式指定文档时用它，保证 provider 开箱即可跑通一次「真渲染」。
DEFAULT_DOCUMENT = (
    Path(__file__).resolve().parents[2] / "render" / "assets" / "s1_probe.html"
)

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


class PlaywrightUnavailable(Exception):
    """Playwright 包未安装（或 Chromium 未下载）。"""


class PlaywrightRenderError(Exception):
    """渲染文档不满足逐帧契约（缺 ``window.__setTime`` / 页面报错 / 时长非法）。"""


def _scene_image_uri(raw: str) -> str:
    """配图路径 → 页面可用的 ``file://`` uri；文件缺失直接报错。

    缺失不降级为「裂图」：配图挂了渲出来就是一张破图，属于静默出坏片，
    必须当场失败让人看见（与 S2「生图失败不是静默空片」同一条原则）。
    """
    path = Path(raw.replace("file://", "")).resolve()
    if not path.is_file():
        raise PlaywrightRenderError(f"scene image not found: {path}")
    return path.as_uri()


def _scenes_payload(scenes: list[RenderScene] | None) -> list[dict[str, Any]]:
    """分幕 → 注入视觉稿的 JSON（``window.SCENES``）。"""
    if not scenes:
        return []
    return [
        {
            "seq": s.seq,
            "text": s.text,
            "highlight": s.highlight,
            "startSec": s.start_sec,
            "durationSec": s.duration_sec,
            "imageUri": _scene_image_uri(s.image_uri) if s.image_uri else None,
        }
        for s in scenes
    ]


def _safe_id(raw: str) -> str:
    """把 ``source_version_id`` 收敛为文件名安全串（防路径穿越）。"""
    return _SAFE_ID.sub("_", raw) or "unknown"


class PlaywrightRenderer:
    """Chromium 逐帧截图 → ffmpeg 管道直连渲染器。"""

    name = "playwright-renderer"
    capability = "render:frame-by-frame-v1"

    def __init__(
        self,
        runner: FFmpegRunner | None = None,
        ffmpeg_bin: str | None = None,
        *,
        document_uri: str | None = None,
        scene_durations: list[float] | None = None,
        duration_seconds: float | None = None,
        max_frames: int | None = None,
        start_sec: float = 0.0,
        output_dir: str | None = None,
        jpeg_quality: int = 92,
        headless: bool = True,
        warmup_ms: int = 800,
        timeout_sec: int = 3600,
    ) -> None:
        """构造渲染器。

        Args:
            runner: ffmpeg 封装（取消/超时/回收语义来源）。
            ffmpeg_bin: 覆盖 ffmpeg 可执行名（测试注入 fake 用）。
            document_uri: 默认视觉文档（``file://`` 或本地路径）。
                ``spec.background_uri`` 非空时以其为准。
            scene_durations: 各幕时长（秒），注入为 ``window.SCENE_DURATIONS``。
            duration_seconds: **显式指定成片时长**；``None`` 时用 ffprobe
                实测音频时长。测试与「只渲片段」场景用这个绕开探测。
            max_frames: 帧数上限（``--test N`` 语义，探路测速用）。
            start_sec: 起始秒（配合 ``max_frames`` 渲片段）。
            output_dir: 成片落盘目录；默认与音频同目录（对齐 FFmpegRenderer）。
            jpeg_quality: 截图质量（92 为画质/体积平衡点）。
            headless: 无头模式。
            warmup_ms: 打开文档后的稳定等待（字体/图片加载）。
            timeout_sec: ffmpeg 收尾最长等待秒数。
        """
        self.runner = runner or FFmpegRunner()
        self.ffmpeg_bin = ffmpeg_bin
        self.document_uri = document_uri
        self.scene_durations = scene_durations
        self.duration_seconds = duration_seconds
        self.max_frames = max_frames
        self.start_sec = start_sec
        self.output_dir = output_dir
        self.jpeg_quality = jpeg_quality
        self.headless = headless
        self.warmup_ms = warmup_ms
        self.timeout_sec = timeout_sec
        #: 最近一次 ffmpeg 子进程句柄（测试据此断言「取消后已回收」）
        self.last_proc: subprocess.Popen[Any] | None = None
        #: 上一次渲染实测到的各幕首句出现秒（``__getSentBorn``）；见 RenderResult
        self.last_born_sec: list[float] = []

    # ------------------------------------------------------------------
    # RendererProvider
    # ------------------------------------------------------------------
    def render(
        self,
        spec: RenderSpec,
        audio_uri: str,
        progress_cb: Callable[[float], None],
        cancel_event: Any,
    ) -> RenderResult:
        """逐帧渲染成片（详见模块 docstring 的三条契约）。"""
        # require_bin（而非 available）：后者只表示「有路径」，前者确认文件存在
        self.runner.require_bin()
        audio_path = audio_uri.replace("file://", "")
        if not os.path.isfile(audio_path):
            raise PlaywrightRenderError(f"audio not found: {audio_path}")

        # 文档解析（S3 起）：
        # 1. 显式文档（spec.design_doc_uri 正名 / background_uri S1 遗留别名 /
        #    构造参数 document_uri 供测试注入）→ 给了就用，**不碰风格**：
        #    style_id 对自定义视觉稿只是说明性字段。
        # 2. 没给显式文档 → 按 spec.style_id 取内置视觉稿（A/B 两版）。
        #    未知风格**当场报错**——绝不像旧行为那样静默回退成另一张画面。
        # 3. DEFAULT_DOCUMENT（S1 探路文档）仅作代码层兜底。
        explicit = (
            spec.design_doc_uri or spec.background_uri or self.document_uri
        )
        if explicit:
            document: str = explicit
        else:
            try:
                document = str(style_document(spec.style_id))
            except KeyError as exc:
                raise PlaywrightRenderError(str(exc)) from None
        doc_path = document.replace("file://", "")
        if not os.path.isfile(doc_path):
            raise PlaywrightRenderError(f"render document not found: {doc_path}")

        total_sec = (
            self.duration_seconds
            if self.duration_seconds is not None
            else self.runner.probe(audio_path)
        )
        fps = spec.fps
        if fps <= 0:
            raise PlaywrightRenderError(f"invalid fps: {fps}")
        nframes = int(math.ceil(total_sec * fps))
        if self.max_frames is not None:
            nframes = min(nframes, self.max_frames)
        if nframes <= 0:
            raise PlaywrightRenderError(
                f"nothing to render: duration={total_sec}s fps={fps}"
            )

        w, h = spec.resolution
        out_dir = self.output_dir or (os.path.dirname(audio_path) or ".")
        video_path = os.path.join(
            out_dir, f"draft_{_safe_id(spec.source_version_id)}.mp4"
        )
        args = [
            "-y", "-hide_banner", "-loglevel", "error",
            # 帧从 stdin 来：不落盘中间帧（见模块 docstring）
            "-f", "image2pipe", "-vcodec", "mjpeg", "-r", str(fps), "-i", "-",
            "-i", audio_path,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            video_path,
        ]
        if self.ffmpeg_bin is not None:
            args = [self.ffmpeg_bin, *args]

        # 每轮渲染先清空：上一轮的实测值绝不能泄漏到这一轮的结果里
        self.last_born_sec = []
        proc = self.runner.spawn(args)
        self.last_proc = proc
        progress_cb(0.0)
        try:
            self._feed_frames(
                proc, nframes, fps, w, h, doc_path, progress_cb, cancel_event,
                spec.scenes,
            )
            _close_stdin(proc)
            self.runner.supervise(
                proc, progress_cb, cancel_event, timeout_sec=self.timeout_sec
            )
        except BaseException:
            # 任何异常路径（取消 / 文档不合规 / 管道断裂）都必须收掉子进程，
            # 否则 ffmpeg 会挂着 stdin 变成孤儿进程。
            _close_stdin(proc)
            _reap(proc)
            raise
        # 帧循环已经把进度推到 1.0；此处不再补发，避免重复终值。

        duration_out = 0.0
        try:
            duration_out = self.runner.probe(video_path)
        except (FFmpegUnavailable, FFmpegFailed):
            # 成片已落盘，时长只是元数据——探不到就如实记 0，不因此判失败。
            duration_out = 0.0
        return RenderResult(
            video_uri="file://" + video_path,
            duration_seconds=duration_out,
            template=spec.template,
            tts_engine=spec.tts_engine.value
            if isinstance(spec.tts_engine, str)
            else str(spec.tts_engine),
            # 视觉稿没暴露 __getSentBorn 时为空列表（「没测到」，不是 0）
            scene_born_sec=self.last_born_sec,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _collect_born_sec(self, page: Any, scene_count: int) -> None:
        """实测各幕首句在画面上的出现秒（视觉稿需暴露 ``__getSentBorn``）。

        抽帧目检撞在切句瞬间会取到空字幕，据此前移取样点（S1 遗留项）。
        取不到（文档没暴露 / 中途返回 null）就**整体不填** —— 半截的
        born 列表比没有更危险，它会让某一幕默默前移错位的秒数。
        """
        if scene_count <= 0:
            return
        if not page.evaluate("typeof window.__getSentBorn === 'function'"):
            return
        born: list[float] = []
        for i in range(scene_count):
            try:
                value = page.evaluate(f"window.__getSentBorn({i})")
            except Exception:  # noqa: BLE001 - 文档脚本异常按「取不到」处理
                return
            if value is None:
                return
            try:
                born.append(float(value))
            except (TypeError, ValueError):
                return
        if len(born) == scene_count:
            self.last_born_sec = born

    def _feed_frames(
        self,
        proc: subprocess.Popen[Any],
        nframes: int,
        fps: int,
        width: int,
        height: int,
        doc_path: str,
        progress_cb: Callable[[float], None],
        cancel_event: Any,
        scenes: list[RenderScene] | None = None,
    ) -> None:
        """逐帧截图写入 ffmpeg stdin；取消时提前返回（不抛，交给 supervise）。"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - 取决于安装
            raise PlaywrightUnavailable(
                "playwright not installed; run `pip install playwright` "
                "and `playwright install chromium`"
            ) from exc

        durations = (
            [s.duration_sec for s in scenes]
            if scenes
            else (self.scene_durations or [])
        )
        url = Path(doc_path).resolve().as_uri()
        with sync_playwright() as pw:
            # --allow-file-access-from-files：视觉稿是 file:// 页面，要显示
            # 同为本机文件的配图（``<img src="file:///...">``）必须开这个，
            # 否则 Chromium 默认拒绝 file→file 访问，图全裂。
            browser = pw.chromium.launch(
                headless=self.headless, args=["--allow-file-access-from-files"]
            )
            try:
                page = browser.new_page(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                )
                page_errors: list[str] = []
                page.on("pageerror", lambda e: page_errors.append(str(e)))
                # SCENE_DURATIONS 是 S1 就有的契约（老视觉稿只认它）；
                # SCENES 是 S2 的完整分幕（文本 / 配图 / 高亮）。
                page.add_init_script(
                    script="window.SCENE_DURATIONS=" + json.dumps(durations) + ";"
                )
                page.add_init_script(
                    script="window.SCENES=" + json.dumps(_scenes_payload(scenes)) + ";"
                )
                page.goto(url)
                page.wait_for_timeout(self.warmup_ms)
                if page_errors:
                    raise PlaywrightRenderError(
                        "page errors: " + "; ".join(page_errors[:5])
                    )
                if not page.evaluate("typeof window.__setTime === 'function'"):
                    raise PlaywrightRenderError(
                        f"document does not define window.__setTime: {doc_path}"
                    )
                self._collect_born_sec(page, len(scenes or []))
                stdin = proc.stdin
                if stdin is None:
                    raise PlaywrightRenderError("ffmpeg stdin unavailable")
                for i in range(nframes):
                    if cancel_event is not None and cancel_event.is_set():
                        return
                    t = self.start_sec + i / fps
                    page.evaluate(f"window.__setTime({t:.4f})")
                    buf = page.screenshot(type="jpeg", quality=self.jpeg_quality)
                    try:
                        stdin.write(buf)
                    except (BrokenPipeError, OSError):
                        # ffmpeg 已退出：交 supervise 抛出 FFmpegFailed（带 stderr）
                        return
                    progress_cb((i + 1) / nframes)
            finally:
                browser.close()


def _close_stdin(proc: subprocess.Popen[Any]) -> None:
    """关闭帧管道（让 ffmpeg 看到 EOF 正常收尾）；已关闭/断裂均忽略。"""
    stdin = proc.stdin
    if stdin is None:
        return
    try:
        if not stdin.closed:
            stdin.close()
    except (BrokenPipeError, OSError):
        pass


def _reap(proc: subprocess.Popen[Any]) -> None:
    """异常路径兜底回收：terminate → 5s → kill → wait（保证 0 僵尸）。"""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
