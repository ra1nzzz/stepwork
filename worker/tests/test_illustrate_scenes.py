"""``IllustrateScenes`` 测试（S2 配图阶段）。

锁死四件事：

1. **接口先于厂商**：handler 只依赖 :class:`ImageProvider` 协议，换厂商不动
   这里 —— 用最小替身（只满足协议）就能跑通，即是证明；
2. **失败必须可见**：任一幕生图失败 → 任务进 ``FAILED`` + 错误可读，
   **不是静默出空片**（S2 验收）；
3. **失败不回滚成功的幕**（生图要钱，全回滚等于白烧）；
4. **重跑默认跳过已有图**（不重复计费）；空幕不生图。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentVersion, JobState
from worker.runtime.providers.image.local import LocalImageProvider

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"
_WS = "ws-il"


class _MinimalImage:
    """只满足 :class:`ImageProvider` 协议的最小替身（无 name 外的任何东西）。"""

    name = "minimal-image"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def generate(self, prompt: str, opts: dict[str, Any] | None = None) -> str:
        self.calls.append((prompt, opts or {}))
        return f"file:///img/{len(self.calls)}.png"


class _FailingImage:
    """第 ``fail_on_call`` 次调用失败（1-based），其余成功。

    按「第几次调用」而不是「哪个 seq」失败：handler 按幕序遍历，调用序即
    幕序，替身不必知道 seq —— 真实 provider 也只收得到 prompt。
    """

    name = "flaky-image"

    def __init__(self, fail_on_call: int) -> None:
        self.fail_on_call = fail_on_call
        self.n = 0

    async def generate(self, prompt: str, opts: dict[str, Any] | None = None) -> str:
        self.n += 1
        if self.n == self.fail_on_call:
            raise RuntimeError("vendor rate limited")
        return f"file:///img/call{self.n}.png"


def _is_file(path: str) -> bool:
    """同步 helper：检查文件存在性（避免 async 测试内触发 ASYNC240）。"""
    return os.path.isfile(path)


def _read_text(path: str) -> str:
    """同步 helper：读文本文件（避免 async 测试内触发 ASYNC230）。"""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _deps(image: Any = None) -> Deps:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    return Deps(repos=Repos(c), image=image)


def _scene_env(command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "commandId": "cmd-il",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u"},
        "source": "ui",
        "workspaceId": _WS,
        "payload": payload,
        "requestedAt": "2026-09-09T00:00:00+00:00",
    }


async def _version_with_scenes(
    deps: Deps, seqs: list[tuple[int, str]]
) -> str:
    deps.repos.workspaces.ensure(_WS)
    pid = deps.repos.projects.get_or_create_default(_WS).id
    vid = deps.repos.content_versions.insert(
        ContentVersion(
            project_id=pid,
            content_type="script",
            content=json.dumps({"title": "T", "body": "b"}, ensure_ascii=False),
            content_hash="h",
            producer={},
        )
    )
    await dispatch(
        _scene_env(
            "SaveVideoScenes",
            {
                "versionId": vid,
                "scenes": [{"seq": s, "text": t} for s, t in seqs],
            },
        ),
        deps,
    )
    return vid


async def test_illustrate_fills_image_uri() -> None:
    image = _MinimalImage()
    deps = _deps(image)
    vid = await _version_with_scenes(deps, [(0, "第一幕。"), (1, "第二幕。")])

    res = await dispatch(_scene_env("IllustrateScenes", {"versionId": vid}), deps)
    assert res["ok"] is True, res.get("error")

    scenes = deps.repos.video_scenes.list_by_version(vid)
    assert [s.image_uri for s in scenes] == [
        "file:///img/1.png",
        "file:///img/2.png",
    ]
    # 提示词带幕文本与风格；opts 带 style / out_dir
    assert image.calls[0][0].startswith("xiaohei 风格插画，画面内容：第一幕。")
    assert image.calls[0][1]["style"] == "xiaohei"
    assert res["detail"]["style"] == "xiaohei"
    assert res["detail"]["count"] == 2


async def test_illustrate_prompt_extra_and_custom_style() -> None:
    image = _MinimalImage()
    deps = _deps(image)
    vid = await _version_with_scenes(deps, [(0, "一幕。")])
    res = await dispatch(
        _scene_env(
            "IllustrateScenes",
            {"versionId": vid, "style": "ink", "promptExtra": "留白多一些"},
        ),
        deps,
    )
    assert res["ok"] is True, res.get("error")
    assert image.calls[0][0] == "ink 风格插画，画面内容：一幕。。留白多一些"
    assert image.calls[0][1]["style"] == "ink"


async def test_partial_failure_is_failed_and_visible() -> None:
    """生图失败 → 任务 FAILED + 错误可读；成功的幕**不回滚**。"""
    image = _FailingImage(fail_on_call=2)  # 第 2 幕（中间那幕）失败
    deps = _deps(image)
    vid = await _version_with_scenes(
        deps, [(0, "第一幕。"), (1, "第二幕。"), (2, "第三幕。")]
    )

    res = await dispatch(_scene_env("IllustrateScenes", {"versionId": vid}), deps)
    assert res["ok"] is False
    assert "ILLUSTRATE_FAILED" in res["error"]
    # 错误里要能看出「几幕失败、哪一幕、为什么」，不是一句笼统的 failed
    assert "1/3" in res["error"]
    assert "rate limited" in res["error"]

    job = deps.repos.jobs.get(res["job_id"])
    assert job is not None
    assert job.state == JobState.FAILED

    # 成功的幕仍然落库（不回滚 = 不重复计费）；失败幕保持 NULL，不被清空
    scenes = deps.repos.video_scenes.list_by_version(vid)
    assert [s.image_uri for s in scenes] == [
        "file:///img/call1.png",
        None,
        "file:///img/call3.png",
    ]


async def test_failure_preserves_existing_image() -> None:
    """失败绝不能把已有产出清成 NULL —— 那是把已有的图弄丢。"""
    deps = _deps(_MinimalImage())
    vid = await _version_with_scenes(deps, [(0, "一幕。"), (1, "二幕。")])
    await dispatch(_scene_env("IllustrateScenes", {"versionId": vid}), deps)
    before = [s.image_uri for s in deps.repos.video_scenes.list_by_version(vid)]
    assert before == ["file:///img/1.png", "file:///img/2.png"]

    # 换成会失败的 provider 并强制重生成：第 1 幕失败，第 2 幕成功
    deps.image = _FailingImage(fail_on_call=1)
    res = await dispatch(
        _scene_env("IllustrateScenes", {"versionId": vid, "force": True}), deps
    )
    assert res["ok"] is False
    after = [s.image_uri for s in deps.repos.video_scenes.list_by_version(vid)]
    # 第 1 幕保留原图；第 2 幕换成新图
    assert after[0] == "file:///img/1.png"
    assert after[1] == "file:///img/call2.png"


async def test_rerun_skips_existing_unless_force() -> None:
    """重跑默认跳过已有图（生图要钱），force 才重生成。"""
    image = _MinimalImage()
    deps = _deps(image)
    vid = await _version_with_scenes(deps, [(0, "第一幕。"), (1, "第二幕。")])
    await dispatch(_scene_env("IllustrateScenes", {"versionId": vid}), deps)
    assert len(image.calls) == 2

    res = await dispatch(_scene_env("IllustrateScenes", {"versionId": vid}), deps)
    assert res["ok"] is True, res.get("error")
    assert len(image.calls) == 2  # 没再调 provider
    assert res["detail"]["skippedSeqs"] == [0, 1]
    assert res["detail"]["count"] == 0

    await dispatch(
        _scene_env("IllustrateScenes", {"versionId": vid, "force": True}), deps
    )
    assert len(image.calls) == 4


async def test_blank_scene_is_skipped() -> None:
    image = _MinimalImage()
    deps = _deps(image)
    vid = await _version_with_scenes(deps, [(0, "有字。"), (1, "   ")])
    res = await dispatch(_scene_env("IllustrateScenes", {"versionId": vid}), deps)
    assert res["ok"] is True, res.get("error")
    assert len(image.calls) == 1
    assert res["detail"]["skippedSeqs"] == [1]


async def test_missing_provider_and_bad_input() -> None:
    deps = _deps(None)
    vid = await _version_with_scenes(deps, [(0, "一幕。")])
    res = await dispatch(_scene_env("IllustrateScenes", {"versionId": vid}), deps)
    assert res["ok"] is False
    assert "UNAVAILABLE" in res["error"]
    # 错误里必须给出「该配什么」，别让人以为是自己配错了
    assert "STEPWORK_IMAGE_PROVIDER" in res["error"]
    assert "cogview" in res["error"] and "siliconflow" in res["error"]

    empty = await _version_with_scenes(deps, [])
    res = await dispatch(_scene_env("IllustrateScenes", {"versionId": empty}), deps)
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]

    res = await dispatch(_scene_env("IllustrateScenes", {"versionId": "cv_x"}), deps)
    assert res["ok"] is False
    assert "NOT_FOUND" in res["error"]


async def test_local_provider_is_deterministic(tmp_path: Path) -> None:
    """本地占位图：同输入 → 同文件；产物是合法 SVG 且含幕文本。"""
    provider = LocalImageProvider(out_dir=str(tmp_path))
    uri1 = await provider.generate("测试画面。", {"style": "ink"})
    uri2 = await provider.generate("测试画面。", {"style": "ink"})
    assert uri1 == uri2
    path = uri1.removeprefix("file://")
    assert _is_file(path)
    content = _read_text(path)
    assert content.startswith("<svg") and "测试画面。" in content
    # 不同风格 → 不同文件（风格进了缓存键）
    assert await provider.generate("测试画面。", {"style": "other"}) != uri1
