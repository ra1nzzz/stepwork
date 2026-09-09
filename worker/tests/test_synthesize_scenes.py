"""``SynthesizeScenes`` 测试（S2 配音阶段：把幕变成有时间轴的幕）。

用**真实的** :class:`LocalTTSProvider`（确定性 WAV、时长真实）而不是手写
假 provider —— 否则「实测时长」变成了「手填时长」，等于什么都没测。

重点锁死：

1. ``start_sec`` 是前序幕实测时长累加，不是估值、不是等分；
2. 时间轴**整批**落库（半新半旧会让渲染错位）；
3. job 必须走到 SUCCEEDED（不新建 content_version 的步骤最容易漏收尾，
   漏了就永远停在 RUNNING）；
4. 拼接失败**可见**（``concatError``）但不拖垮整条命令 —— 事实是每幕时长。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentVersion, JobState
from worker.runtime.providers.tts.local import LocalTTSProvider
from worker.runtime.render.ffmpeg_runner import FFmpegRunner
from worker.runtime.render.subtitles import probe_audio_duration

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"
_WS = "ws-ss"


class _BogusRenderer:
    """渲染器替身：带一个**指向不存在的 ffmpeg** 的 runner。

    用来验证「拼接失败要可见」——FFmpegRunner 构造时只认路径非空，
    路径是不是真文件要到 require_bin() 才现形。
    """

    name = "bogus-renderer"

    def __init__(self) -> None:
        self.runner = FFmpegRunner("C:/no/such/ffmpeg.exe")


def _is_file(path: str) -> bool:
    """同步 helper：检查文件存在性（避免 async 测试内触发 ASYNC240）。"""
    return os.path.isfile(path)


def _deps(tmp_path: Path, *, tts: Any = None, renderer: Any = None) -> Deps:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    return Deps(
        repos=Repos(c),
        tts=tts if tts is not None else LocalTTSProvider(),
        renderer=renderer,
    )


def _script_version(deps: Deps, body: str = "第一幕的话。\n\n第二幕的话。\n\n第三幕的话。") -> str:
    deps.repos.workspaces.ensure(_WS)
    pid = deps.repos.projects.get_or_create_default(_WS).id
    return deps.repos.content_versions.insert(
        ContentVersion(
            project_id=pid,
            content_type="script",
            content=json.dumps({"title": "T", "body": body}, ensure_ascii=False),
            content_hash="h",
            producer={},
        )
    )


def _env(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "commandId": "cmd-ss",
        "commandType": "SynthesizeScenes",
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u"},
        "source": "ui",
        "workspaceId": _WS,
        "payload": payload,
        "requestedAt": "2026-09-09T00:00:00+00:00",
    }


async def test_synth_fills_audio_and_measured_duration(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    vid = _script_version(deps)
    # 先有幕（GenerateScript 路径已在 test_script_scenes 覆盖，这里直接落）
    res = await dispatch(
        {
            **_env({}),
            "commandType": "SaveVideoScenes",
            "payload": {
                "versionId": vid,
                "scenes": [
                    {"seq": 0, "text": "第一幕的话。"},
                    {"seq": 1, "text": "第二幕的话稍微长一些。"},
                    {"seq": 2, "text": "第三幕。"},
                ],
            },
        },
        deps,
    )
    assert res["ok"] is True, res.get("error")

    res = await dispatch(_env({"versionId": vid, "outDir": str(tmp_path)}), deps)
    assert res["ok"] is True, res.get("error")

    scenes = deps.repos.video_scenes.list_by_version(vid)
    assert len(scenes) == 3
    for s in scenes:
        assert s.audio_uri is not None and s.audio_uri.startswith("file://")
        # 实测时长：与文件真实时长一致，不是估值
        assert s.duration_sec > 0
        assert abs(s.duration_sec - probe_audio_duration(
            s.audio_uri.removeprefix("file://")
        )) < 1e-6


async def test_start_sec_is_cumulative(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    vid = _script_version(deps)
    await dispatch(
        {
            **_env({}),
            "commandType": "SaveVideoScenes",
            "payload": {
                "versionId": vid,
                "scenes": [
                    {"seq": 0, "text": "短。"},
                    {"seq": 1, "text": "这一幕明显更长一些。"},
                    {"seq": 2, "text": "收尾。"},
                ],
            },
        },
        deps,
    )
    await dispatch(_env({"versionId": vid, "outDir": str(tmp_path)}), deps)

    scenes = deps.repos.video_scenes.list_by_version(vid)
    assert scenes[0].start_sec == 0.0
    assert abs(scenes[1].start_sec - scenes[0].duration_sec) < 1e-9
    assert abs(
        scenes[2].start_sec - (scenes[0].duration_sec + scenes[1].duration_sec)
    ) < 1e-9
    # 长幕 > 短幕：证明是实测而非等分
    assert scenes[1].duration_sec > scenes[0].duration_sec


async def test_detail_reports_timeline_and_total(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    vid = _script_version(deps)
    await dispatch(
        {
            **_env({}),
            "commandType": "SaveVideoScenes",
            "payload": {"versionId": vid, "scenes": [{"seq": 0, "text": "一句话。"}]},
        },
        deps,
    )
    res = await dispatch(_env({"versionId": vid, "outDir": str(tmp_path)}), deps)
    assert res["ok"] is True, res.get("error")
    detail = res["detail"]
    assert detail["count"] == 1
    assert detail["scenes"][0]["startSec"] == 0.0
    assert detail["scenes"][0]["durationSec"] > 0
    assert abs(detail["totalDurationSec"] - detail["scenes"][0]["durationSec"]) < 1e-6


async def test_job_reaches_succeeded(tmp_path: Path) -> None:
    """不新建 content_version 的步骤最容易漏收尾 → job 永远停在 RUNNING。"""
    deps = _deps(tmp_path)
    vid = _script_version(deps)
    await dispatch(
        {
            **_env({}),
            "commandType": "SaveVideoScenes",
            "payload": {"versionId": vid, "scenes": [{"seq": 0, "text": "一句话。"}]},
        },
        deps,
    )
    res = await dispatch(_env({"versionId": vid, "outDir": str(tmp_path)}), deps)
    job = deps.repos.jobs.get(res["job_id"])
    assert job is not None
    assert job.state == JobState.SUCCEEDED
    assert job.stage is not None and job.stage.value == "synthesizing"
    assert job.progress == 1.0


async def test_no_scenes_is_rejected(tmp_path: Path) -> None:
    """没幕就成功 = 调用方以为时间轴就绪，渲染时才发现全 0。"""
    deps = _deps(tmp_path)
    vid = _script_version(deps)
    res = await dispatch(_env({"versionId": vid}), deps)
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]


async def test_unknown_version_is_rejected(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    res = await dispatch(_env({"versionId": "cv_nope"}), deps)
    assert res["ok"] is False
    assert "NOT_FOUND" in res["error"]


async def test_missing_tts_is_rejected(tmp_path: Path) -> None:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    deps = Deps(repos=Repos(c), tts=None)
    vid = _script_version(deps)
    # 先要有幕：输入校验（没幕）优先于能力校验（没 TTS），
    # 否则测到的就不是「TTS 缺失」这条分支
    await dispatch(
        {
            **_env({}),
            "commandType": "SaveVideoScenes",
            "payload": {"versionId": vid, "scenes": [{"seq": 0, "text": "一句话。"}]},
        },
        deps,
    )
    res = await dispatch(_env({"versionId": vid}), deps)
    assert res["ok"] is False
    assert "UNAVAILABLE" in res["error"]


async def test_empty_text_scene_is_skipped_not_silent(tmp_path: Path) -> None:
    """空幕不该凭空多出一段静音，把后面所有幕的 start_sec 推歪。"""
    deps = _deps(tmp_path)
    vid = _script_version(deps)
    await dispatch(
        {
            **_env({}),
            "commandType": "SaveVideoScenes",
            "payload": {
                "versionId": vid,
                "scenes": [
                    {"seq": 0, "text": "有字的一幕。"},
                    {"seq": 1, "text": ""},
                    {"seq": 2, "text": "最后一幕。"},
                ],
            },
        },
        deps,
    )
    res = await dispatch(_env({"versionId": vid, "outDir": str(tmp_path)}), deps)
    assert res["ok"] is True, res.get("error")
    assert res["detail"]["skippedSeqs"] == [1]

    scenes = deps.repos.video_scenes.list_by_version(vid)
    assert scenes[1].audio_uri is None
    assert scenes[1].duration_sec == 0.0
    # 空幕不占位：第 3 幕紧接第 1 幕
    assert abs(scenes[2].start_sec - scenes[0].duration_sec) < 1e-9


async def test_rerun_recomputes_timeline(tmp_path: Path) -> None:
    """重跑必须是重算，不能在上次结果上累加。"""
    deps = _deps(tmp_path)
    vid = _script_version(deps)
    await dispatch(
        {
            **_env({}),
            "commandType": "SaveVideoScenes",
            "payload": {
                "versionId": vid,
                "scenes": [{"seq": 0, "text": "甲。"}, {"seq": 1, "text": "乙。"}],
            },
        },
        deps,
    )
    await dispatch(_env({"versionId": vid, "outDir": str(tmp_path)}), deps)
    first = deps.repos.video_scenes.list_by_version(vid)
    await dispatch(_env({"versionId": vid, "outDir": str(tmp_path)}), deps)
    second = deps.repos.video_scenes.list_by_version(vid)
    assert [s.start_sec for s in first] == [s.start_sec for s in second]
    assert [s.duration_sec for s in first] == [s.duration_sec for s in second]


async def test_no_concat_flag_skips_concat(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    vid = _script_version(deps)
    await dispatch(
        {
            **_env({}),
            "commandType": "SaveVideoScenes",
            "payload": {"versionId": vid, "scenes": [{"seq": 0, "text": "一句话。"}]},
        },
        deps,
    )
    res = await dispatch(
        _env({"versionId": vid, "outDir": str(tmp_path), "concat": False}), deps
    )
    assert res["ok"] is True, res.get("error")
    assert res["detail"]["audioUri"] is None
    assert res["detail"]["concatError"] is None
    # 时间轴照样落库 —— 整轨只是 convenience
    assert deps.repos.video_scenes.list_by_version(vid)[0].duration_sec > 0


async def test_concat_failure_is_visible_not_silent(tmp_path: Path) -> None:
    """ffmpeg 不在时命令仍成功（事实已落库），但错误必须写在 detail 里。"""
    deps = _deps(tmp_path, renderer=_BogusRenderer())
    vid = _script_version(deps)
    await dispatch(
        {
            **_env({}),
            "commandType": "SaveVideoScenes",
            "payload": {"versionId": vid, "scenes": [{"seq": 0, "text": "一句话。"}]},
        },
        deps,
    )
    res = await dispatch(_env({"versionId": vid, "outDir": str(tmp_path)}), deps)
    assert res["ok"] is True, res.get("error")
    assert res["detail"]["audioUri"] is None
    assert res["detail"]["concatError"]
    # 关键：时间轴不受拼接失败影响
    assert deps.repos.video_scenes.list_by_version(vid)[0].duration_sec > 0


@pytest.mark.skipif(
    not FFmpegRunner(bin_path=os.environ.get("STEPWORK_FFMPEG_BIN")).available,
    reason="需要本机 ffmpeg（可用 STEPWORK_FFMPEG_BIN 指定）",
)
async def test_concat_produces_single_track(tmp_path: Path) -> None:
    """真拼接：整轨时长 ≈ 各幕实测时长之和（渲染器今天就能吃）。"""
    deps = _deps(tmp_path)
    vid = _script_version(deps)
    await dispatch(
        {
            **_env({}),
            "commandType": "SaveVideoScenes",
            "payload": {
                "versionId": vid,
                "scenes": [
                    {"seq": 0, "text": "第一幕的话。"},
                    {"seq": 1, "text": "第二幕的话稍微长一些。"},
                ],
            },
        },
        deps,
    )
    res = await dispatch(_env({"versionId": vid, "outDir": str(tmp_path)}), deps)
    assert res["ok"] is True, res.get("error")
    uri = res["detail"]["audioUri"]
    assert uri and uri.startswith("file://")
    path = uri.removeprefix("file://")
    assert _is_file(path)

    runner = FFmpegRunner(bin_path=os.environ.get("STEPWORK_FFMPEG_BIN"))
    scenes = deps.repos.video_scenes.list_by_version(vid)
    expected = sum(s.duration_sec for s in scenes)
    actual = runner.probe(path)
    assert actual > 0
    # 重编码 + AAC 帧对齐会有小误差，0.5s 内可接受
    assert abs(actual - expected) < 0.5
