"""S2：渲染侧消费分幕（画面按幕切换）。

锁死三件事：

1. **handler 负责组装、provider 不碰 DB** —— 渲染器只从 ``RenderSpec.scenes``
   拿数据（换渲染器实现不需要连数据库）；
2. **时长为 0 的幕不能喂给渲染器** —— 那是没配音的半截数据，画面会与音频错位；
3. **``born_at_sec`` 只有长度严格对齐才写** —— 半截列表会让某一幕默默
   前移错位的秒数，比不写更危险。

真实 Chromium 的用例放 ``test_playwright_renderer.py``（需要浏览器）；
本文件用替身渲染器把**数据流**锁死，不依赖浏览器，永远跑。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.bootstrap import MIGRATIONS_DIR
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import (
    CommandEnvelope,
    ContentProject,
    ContentVersion,
    RenderResult,
    RenderScene,
    RenderSpec,
    VideoScene,
)
from worker.runtime.providers.renderer.playwright import (
    PlaywrightRenderError,
    _scenes_payload,
)


class _CapturingRenderer:
    """替身渲染器：只记录收到的 spec，并按预设返回 born 秒。"""

    name = "capturing-renderer"

    def __init__(self, born: list[float] | None = None) -> None:
        self.spec: RenderSpec | None = None
        self.audio_uri: str | None = None
        self._born = born or []

    def render(
        self,
        spec: RenderSpec,
        audio_uri: str,
        progress_cb: Any,
        cancel_event: Any,
    ) -> RenderResult:
        self.spec = spec
        self.audio_uri = audio_uri
        return RenderResult(
            video_uri="file:///tmp/out.mp4",
            duration_seconds=10.0,
            template=spec.template,
            tts_engine="synthesize",
            scene_born_sec=list(self._born),
        )


def _deps(renderer: Any) -> Deps:
    c = in_memory()
    run_migrations(c, MIGRATIONS_DIR)
    return Deps(repos=Repos(c), renderer=renderer)


def _env(payload: dict[str, Any], project_id: str) -> dict[str, Any]:
    return CommandEnvelope(
        commandId="cmd-rs",
        commandType="CreateRenderJob",
        actor={"type": "user", "id": "u1"},
        source="ui",
        workspaceId=_WS,
        projectId=project_id,
        payload=payload,
        requestedAt="2026-09-09T00:00:00+00:00",
    ).model_dump()


_WS = "ws-rs"


def _seed(deps: Deps) -> tuple[str, str]:
    """建 workspace + project + 一条 script 版本；返回 ``(version_id, project_id)``。"""
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
    return vid, prj


def _render_payload(version_id: str) -> dict[str, Any]:
    """user_audio 路径：整轨由 SynthesizeScenes 产出，渲染不再自己调 TTS。"""
    return {
        "source_version_id": version_id,
        "tts_engine": "user_audio",
        "user_audio_uri": "file:///tmp/vocal.m4a",
    }


def _seed_scenes(deps: Deps, version_id: str, rows: list[dict[str, Any]]) -> None:
    deps.repos.video_scenes.replace_for_version(
        version_id, [VideoScene(version_id=version_id, **r) for r in rows]
    )


# ----- 注入页面的分幕数据（纯函数）-----


def test_scenes_payload_converts_image_to_file_uri(tmp_path: Path) -> None:
    img = tmp_path / "a.svg"
    img.write_text("<svg/>", encoding="utf-8")
    payload = _scenes_payload(
        [
            RenderScene(
                seq=0,
                text="第一幕",
                start_sec=0.0,
                duration_sec=1.5,
                image_uri=str(img),
            )
        ]
    )
    assert payload[0]["text"] == "第一幕"
    assert payload[0]["durationSec"] == 1.5
    # 必须是 file:// 且用正斜杠（file:// 页面直接吃本地路径会被 Chromium 拒）
    assert payload[0]["imageUri"].startswith("file:///")
    assert "\\" not in payload[0]["imageUri"]


def test_scenes_payload_rejects_missing_image(tmp_path: Path) -> None:
    """配图缺失必须当场失败 —— 渲出一张裂图属于静默出坏片。"""
    with pytest.raises(PlaywrightRenderError, match="scene image not found"):
        _scenes_payload(
            [RenderScene(seq=0, text="x", image_uri=str(tmp_path / "nope.png"))]
        )


def test_scenes_payload_empty_when_no_scenes() -> None:
    assert _scenes_payload(None) == []
    assert _scenes_payload([]) == []


# ----- handler → provider 的数据流 -----


async def test_render_passes_scenes_to_renderer() -> None:
    renderer = _CapturingRenderer()
    deps = _deps(renderer)
    vid, prj = _seed(deps)
    _seed_scenes(
        deps,
        vid,
        [
            {"seq": 0, "text": "第一幕。", "start_sec": 0.0, "duration_sec": 1.25},
            {
                "seq": 1,
                "text": "第二幕。",
                "start_sec": 1.25,
                "duration_sec": 4.5,
                "image_uri": "file:///tmp/art.svg",
            },
        ],
    )
    out = await dispatch(_env(_render_payload(vid), prj), deps)
    assert out["ok"] is True, out

    scenes = renderer.spec.scenes if renderer.spec else None
    assert scenes is not None and len(scenes) == 2
    assert scenes[0].text == "第一幕。"
    assert scenes[1].start_sec == 1.25
    assert scenes[1].duration_sec == 4.5
    assert scenes[1].image_uri == "file:///tmp/art.svg"
    # 带行 id，渲染实测的 born_at_sec 才能回填到对应幕
    assert scenes[0].id and scenes[1].id


async def test_scenes_without_duration_are_excluded() -> None:
    """没配音的幕（duration=0）喂给渲染器 = 画面与音频错位。"""
    renderer = _CapturingRenderer()
    deps = _deps(renderer)
    vid, prj = _seed(deps)
    _seed_scenes(
        deps,
        vid,
        [
            {"seq": 0, "text": "有配音。", "duration_sec": 2.0},
            {"seq": 1, "text": "没配音。", "duration_sec": 0.0},
        ],
    )
    out = await dispatch(_env(_render_payload(vid), prj), deps)
    assert out["ok"] is True, out
    scenes = renderer.spec.scenes if renderer.spec else None
    assert scenes is not None and len(scenes) == 1
    assert scenes[0].text == "有配音。"


async def test_no_scenes_keeps_legacy_behavior() -> None:
    """没有分幕时 spec.scenes 为 None —— 老调用方（FFmpegRenderer）不受影响。"""
    renderer = _CapturingRenderer()
    deps = _deps(renderer)
    vid, prj = _seed(deps)
    out = await dispatch(_env(_render_payload(vid), prj), deps)
    assert out["ok"] is True, out
    assert renderer.spec is not None
    assert renderer.spec.scenes is None


async def test_born_at_sec_written_when_aligned() -> None:
    renderer = _CapturingRenderer(born=[0.0, 1.25])
    deps = _deps(renderer)
    vid, prj = _seed(deps)
    _seed_scenes(
        deps,
        vid,
        [
            {"seq": 0, "text": "一。", "start_sec": 0.0, "duration_sec": 1.25},
            {"seq": 1, "text": "二。", "start_sec": 1.25, "duration_sec": 4.5},
        ],
    )
    out = await dispatch(_env(_render_payload(vid), prj), deps)
    assert out["ok"] is True, out
    born = [s.born_at_sec for s in deps.repos.video_scenes.list_by_version(vid)]
    assert born == [0.0, 1.25]


async def test_born_at_sec_skipped_when_misaligned() -> None:
    """半截 born 列表比没有更危险 —— 宁可不写。"""
    renderer = _CapturingRenderer(born=[0.0])  # 只有 1 个，但有 2 幕
    deps = _deps(renderer)
    vid, prj = _seed(deps)
    _seed_scenes(
        deps,
        vid,
        [
            {"seq": 0, "text": "一。", "duration_sec": 1.0},
            {"seq": 1, "text": "二。", "duration_sec": 2.0},
        ],
    )
    out = await dispatch(_env(_render_payload(vid), prj), deps)
    assert out["ok"] is True, out
    born = [s.born_at_sec for s in deps.repos.video_scenes.list_by_version(vid)]
    assert born == [None, None]


def _pid(deps: Deps) -> str:
    return deps.repos.projects.get_or_create_default("ws-rs").id
