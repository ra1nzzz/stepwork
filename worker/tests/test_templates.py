"""渲染模板与画幅注册表测试（PRD-REN-005）。

回归重点：此前 template 完全被渲染器忽略（硬编码一条 argv），UI 切换
模板画面完全相同。这里锁死「不同模板必须产出不同 ffmpeg 参数」与
「未知模板/画幅必须干净拒绝」。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import (
    ContentProject,
    ContentVersion,
    RenderSpec,
    Workspace,
)
from worker.runtime.providers.renderer.ffmpeg import FFmpegRenderer
from worker.runtime.providers.tts.local import LocalTTSProvider
from worker.runtime.render.templates import (
    ASPECT_PRESETS,
    TEMPLATES,
    list_templates,
    resolve_resolution,
    resolve_template,
)

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def test_resolve_template_known_and_unknown() -> None:
    tpl = resolve_template("vertical-caption-v1")
    assert tpl.id == "vertical-caption-v1"
    assert tpl.default_aspect == "9:16"
    with pytest.raises(KeyError, match="unknown template"):
        resolve_template("no-such-template")


def test_resolve_resolution_presets() -> None:
    assert resolve_resolution("9:16") == (1080, 1920)
    assert resolve_resolution("16:9") == (1920, 1080)
    assert resolve_resolution("1:1") == (1080, 1080)
    with pytest.raises(KeyError, match="unknown aspect"):
        resolve_resolution("4:3")


def test_list_templates_exposes_all() -> None:
    listed = {t["id"] for t in list_templates()}
    assert listed == set(TEMPLATES)
    # 每项都带标签与默认画幅，供 UI 直接渲染
    for item in list_templates():
        assert item["label"]
        assert item["default_aspect"] in ASPECT_PRESETS


class _CapturingRunner:
    """记录 ffmpeg argv 的假 runner（不起真实子进程）。"""

    available = True

    def __init__(self) -> None:
        self.args: list[str] = []

    def run(
        self, args: list[str], progress_cb: Any, cancel_event: Any
    ) -> None:
        self.args = args


def _render_args(template: str, resolution: tuple[int, int]) -> list[str]:
    runner = _CapturingRunner()
    renderer = FFmpegRenderer(runner)  # type: ignore[arg-type]
    spec = RenderSpec(
        source_version_id="cv-1",
        template=template,
        resolution=resolution,
        caption_text="标题",
    )
    renderer.render(spec, "file:///tmp/a.wav", lambda _p: None, threading.Event())
    return runner.args


def test_different_templates_produce_different_ffmpeg_args() -> None:
    """核心回归：模板必须真实影响渲染参数（旧实现两者完全相同）。"""
    caption = _render_args("vertical-caption-v1", (1080, 1920))
    story = _render_args("vertical-story-v1", (1080, 1920))
    assert caption != story
    # 背景色与字号来自模板
    assert any("color=c=navy" in a for a in caption)
    assert any("color=c=black" in a for a in story)
    assert any("fontsize=48" in a for a in caption)
    assert any("fontsize=64" in a for a in story)


def test_resolution_flows_into_ffmpeg_args() -> None:
    landscape = _render_args("landscape-caption-v1", (1920, 1080))
    assert any("1920x1080" in a for a in landscape)
    square = _render_args("square-caption-v1", (1080, 1080))
    assert any("1080x1080" in a for a in square)


def _deps() -> tuple[Deps, str, str]:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos = Repos(c)
    ws = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    cv = repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            content_type="transcript",
            content="文本",
            content_hash="h",
        )
    )
    deps = Deps(
        repos=repos,
        ingest=ingest,
        # 渲染路径需要 TTS 合成旁白；本地 provider 零配置可用
        tts=LocalTTSProvider(),
        renderer=FFmpegRenderer(_CapturingRunner()),  # type: ignore[arg-type]
    )
    return deps, prj, cv


def _env(payload: dict[str, Any], prj: str) -> dict[str, Any]:
    return {
        "commandId": "cmd-t",
        "commandType": "CreateRenderJob",
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u"},
        "source": "ui",
        "workspaceId": "ws-t",
        "projectId": prj,
        "payload": payload,
        "requestedAt": "2026-07-26T00:00:00+00:00",
    }


async def test_unknown_template_rejected_not_silently_substituted() -> None:
    deps, prj, cv = _deps()
    res = await dispatch(
        _env({"source_version_id": cv, "template": "bogus-template"}, prj), deps
    )
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]
    assert "unknown template" in res["error"]


async def test_unknown_aspect_rejected() -> None:
    deps, prj, cv = _deps()
    res = await dispatch(
        _env({"source_version_id": cv, "aspect": "4:3"}, prj), deps
    )
    assert res["ok"] is False
    assert "unknown aspect" in res["error"]


async def test_list_render_templates_command() -> None:
    deps, prj, _cv = _deps()
    env = _env({}, prj)
    env["commandType"] = "ListRenderTemplates"
    res = await dispatch(env, deps)
    assert res["ok"] is True
    ids = {t["id"] for t in res["detail"]["templates"]}
    assert "vertical-caption-v1" in ids
    aspects = {a["id"] for a in res["detail"]["aspects"]}
    assert aspects == set(ASPECT_PRESETS)


async def test_template_default_aspect_applies_when_not_specified() -> None:
    """回归（R5）：未显式给 aspect/resolution 时用**模板自己的默认画幅**。

    此前一律回落 RenderSpec 的全局默认 9:16，选横屏/方图模板仍渲成竖屏，
    模板的 default_aspect 形同虚设。
    """
    deps, prj, cv = _deps()
    runner = deps.renderer.runner

    res = await dispatch(
        _env({"source_version_id": cv, "template": "landscape-caption-v1"}, prj), deps
    )
    assert res["ok"] is True, res.get("error")
    assert any("1920x1080" in a for a in runner.args), runner.args


async def test_explicit_aspect_overrides_template_default() -> None:
    """显式 aspect 优先于模板默认（用户明确选择不被覆盖）。"""
    deps, prj, cv = _deps()
    runner = deps.renderer.runner

    res = await dispatch(
        _env(
            {
                "source_version_id": cv,
                "template": "landscape-caption-v1",
                "aspect": "1:1",
            },
            prj,
        ),
        deps,
    )
    assert res["ok"] is True, res.get("error")
    assert any("1080x1080" in a for a in runner.args), runner.args


async def test_vertical_template_still_defaults_to_9_16() -> None:
    """竖屏模板的默认画幅仍是 9:16（不得回归）。"""
    deps, prj, cv = _deps()
    runner = deps.renderer.runner
    res = await dispatch(
        _env({"source_version_id": cv, "template": "vertical-caption-v1"}, prj), deps
    )
    assert res["ok"] is True
    assert any("1080x1920" in a for a in runner.args), runner.args


def test_frontend_fallback_template_list_matches_backend() -> None:
    """前端兜底模板清单必须与后端注册表同名（否则用户会选到后端不认的模板）。

    复核发现旧渲染视图硬编码只有一个模板，与后端 4 个不一致。
    """
    import re

    ts = (
        Path(__file__).resolve().parents[2]
        / "apps/desktop/src/lib/renderTemplates.ts"
    ).read_text(encoding="utf-8")
    block = ts.split("RENDER_TEMPLATE_FALLBACK")[1]
    frontend = set(re.findall(r'"([a-z0-9-]+)"', block))
    assert frontend == set(TEMPLATES), (
        f"前端兜底清单与后端注册表漂移：前端={sorted(frontend)} "
        f"后端={sorted(TEMPLATES)}"
    )
