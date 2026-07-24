"""W9 L.42 性能基线测试。

走进程内 ``dispatch``（同 test_e2e），用 ``time.perf_counter`` 测量每条命令
端到端耗时，建立可重复测量的基线。默认不跑（``@pytest.mark.perf``），
手动执行建立基线并回填到 ``docs/PERF_BASELINE.md``：

    python -m pytest worker/tests/test_performance.py -m perf -s

设计决策（W9_PLAN D5）：

- **不在 CI 跑**：性能测试在不同机器/CI 抖动大，作为常规门禁会引入噪声。
- **不使用 pytest-benchmark**：MVP 不引新依赖；``perf_counter`` 足以建立
  数量级基线（毫秒级），V0.2 视需要再升级到 ``pytest-benchmark``。
- **稳态取中位数**：每次独立 setup（新 in_memory DB），跑 N 次丢弃 warmup
  后取中位数，降噪同时不掩盖异常。
- **fake providers 路径**：与 test_e2e 一致，避免真实外部依赖造成抖动。
- **测试函数走 sync def + asyncio.run**：性能测量需要 sync 控制循环；
  pytest-asyncio 的 event loop 与内部 ``asyncio.run`` 互斥（同 loop 嵌套
  会抛 RuntimeError），故测试函数本身用 ``def`` 而非 ``async def``。
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentProject, Workspace
from worker.runtime.providers.asr.local import LocalASRProvider
from worker.runtime.providers.renderer.ffmpeg import FFmpegRenderer
from worker.runtime.providers.tts.local import LocalTTSProvider
from worker.runtime.render.ffmpeg_runner import FFmpegRunner

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"
_FAKE_FFMPEG = os.path.join(os.path.dirname(__file__), "fakes", "fake_ffmpeg.py")
_PY = sys.executable

# 稳态测量重复次数（W9_PLAN R6：取中位数降噪）
_WARMUP_ITERS = 2
_STEADY_ITERS = 5

# 允许区间上限（毫秒）——基线文档记录实测中位数，CI/手动跑时若超出此阈值
# 视为性能回归，需调查。值为 fake providers 下的宽松上限，真实 provider
# 走 docs/PERF_BASELINE.md 单独记录。
_THRESHOLDS_MS: dict[str, float] = {
    "ImportSource": 50.0,
    "TranscribeSource": 80.0,
    "AnalyzeSource": 80.0,  # 含 import+transcribe 前置开销
    "GetProvenance": 30.0,
    "ExportProject": 100.0,
    "FullPipelineColdStart": 800.0,  # 7 条命令之和 × 余量
}


class _FakeAI:
    """与 test_e2e 一致的确定性 AI Provider（按 schema 形状分流）。"""

    name = "fake-ai"
    model = "fake-1"
    estimated_cost_per_1k = 0.0

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if schema and schema.get("title") == "AnalysisReport":
            return {
                "summary": "性能基线测试摘要。",
                "topics": ["性能", "基线"],
                "sentiment": "neutral",
                "suggested_title": "性能基线测试",
                "suggested_tags": ["perf"],
                "key_points": ["冷启动", "稳态"],
                "target_audience": "工程师",
                "provider": "fake",
                "model": "fake-1",
                "confidence": 0.9,
            }
        props = (schema or {}).get("properties", {}) if schema else {}
        if "angles" in props:
            return {
                "angles": [
                    {
                        "id": "a1",
                        "title": "性能基线切入点",
                        "rationale": "可测量即可优化",
                        "hook": "性能问题怎么定位？",
                    }
                ],
                "title": "性能基线切入点",
                "body": "（0-3s）钩子\n（3-10s）方法\n（10-15s）收尾",
            }
        return {
            "title": "性能基线切入点",
            "body": "（0-3s）钩子\n（3-10s）方法\n（10-15s）收尾",
        }


def _deps() -> Deps:
    """构造全链路所需依赖（in_memory DB + fake providers）。"""
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    return Deps(
        repos=Repos(c),
        ingest=ingest,
        asr=LocalASRProvider(),
        ai=_FakeAI(),
        tts=LocalTTSProvider(),
        renderer=FFmpegRenderer(FFmpegRunner(bin_path=_PY), ffmpeg_bin=_FAKE_FFMPEG),
    )


def _env(
    command_type: str,
    payload: dict[str, Any],
    *,
    workspace_id: str = "ws-perf",
    project_id: str | None = None,
    command_id: str = "cmd-perf",
) -> dict[str, Any]:
    return {
        "commandId": command_id,
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u-perf"},
        "source": "ui",
        "workspaceId": workspace_id,
        "projectId": project_id,
        "payload": payload,
        "requestedAt": "2026-07-23T00:00:00+00:00",
    }


def _seed_project(deps: Deps) -> tuple[str, str]:
    ws_id = deps.repos.workspaces.insert(
        Workspace(name="ws-perf", root_path="/tmp/perf")
    )
    prj_id = deps.repos.projects.insert(
        ContentProject(workspace_id=ws_id, title="Perf 基线项目")
    )
    return ws_id, prj_id


def _steady_median_ms(
    setup: Any, run: Any, *, name: str
) -> float:
    """稳态测量：每次重新 setup，跑 N 次取中位数。

    ``setup`` / ``run`` 均为返回 coroutine 的 sync 函数；每个 iteration 用
    独立 ``asyncio.run`` 执行（独立 event loop，避免跨 iteration 状态泄漏）。

    Args:
        setup: 返回 ``(deps, ws_id, prj_id, ctx)`` 的 sync 函数。
        run: 接收 setup 返回值、返回 coroutine 的 sync 函数。
        name: 命令名（用于打印）。
    """
    samples: list[float] = []
    for i in range(_WARMUP_ITERS + _STEADY_ITERS):
        deps, ws_id, prj_id, ctx = setup()
        t0 = time.perf_counter()
        asyncio.run(run(deps, ws_id, prj_id, ctx))
        elapsed = (time.perf_counter() - t0) * 1000.0
        if i >= _WARMUP_ITERS:
            samples.append(elapsed)
    median = statistics.median(samples)
    print(f"  [perf] {name}: median={median:.2f}ms (n={len(samples)})")
    return median


def _assert_perf(name: str, median_ms: float) -> None:
    """断言性能中位数在阈值内；超出时打印回归提示。"""
    threshold = _THRESHOLDS_MS.get(name, float("inf"))
    assert median_ms <= threshold, (
        f"{name} perf regression: median={median_ms:.2f}ms > "
        f"threshold={threshold:.0f}ms"
    )


# ---------------------------------------------------------------------------
# 单命令稳态测量
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_perf_import_source() -> None:
    """ImportSource 稳态耗时基线。"""
    counter = {"i": 0}

    def setup() -> tuple[Deps, str, str, dict[str, Any]]:
        deps = _deps()
        ws_id, prj_id = _seed_project(deps)
        counter["i"] += 1
        ctx = {"content_hash": f"hash-perf-{counter['i']}"}
        return deps, ws_id, prj_id, ctx

    async def run(
        deps: Deps, ws_id: str, prj_id: str, ctx: dict[str, Any]
    ) -> None:
        res = await dispatch(
            _env(
                "ImportSource",
                {
                    "local_uri": f"file:///tmp/perf/{ctx['content_hash']}.mp4",
                    "content_hash": ctx["content_hash"],
                    "kind": "video",
                },
                workspace_id=ws_id,
                project_id=prj_id,
            ),
            deps,
        )
        assert res["ok"], res

    median = _steady_median_ms(setup, run, name="ImportSource")
    _assert_perf("ImportSource", median)


@pytest.mark.perf
def test_perf_transcribe_source() -> None:
    """TranscribeSource 稳态耗时基线（含 LocalASRProvider）。"""

    def setup() -> tuple[Deps, str, str, str]:
        deps = _deps()
        ws_id, prj_id = _seed_project(deps)
        # 预先 ImportSource 拿到 asset_id（不计入 TranscribeSource 测量）
        r = asyncio.run(
            dispatch(
                _env(
                    "ImportSource",
                    {
                        "local_uri": "file:///tmp/perf/t.mp4",
                        "content_hash": f"hash-perf-t-{time.perf_counter_ns()}",
                        "kind": "video",
                    },
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
        )
        assert r["ok"], r
        return deps, ws_id, prj_id, r["artifact_ids"][0]

    async def run(deps: Deps, ws_id: str, prj_id: str, asset_id: str) -> None:
        res = await dispatch(
            _env(
                "TranscribeSource",
                {"asset_id": asset_id, "opts": {"duration_sec": 12}},
                workspace_id=ws_id,
                project_id=prj_id,
            ),
            deps,
        )
        assert res["ok"], res

    median = _steady_median_ms(setup, run, name="TranscribeSource")
    _assert_perf("TranscribeSource", median)


@pytest.mark.perf
def test_perf_analyze_source() -> None:
    """AnalyzeSource 稳态耗时基线。

    AnalyzeSource 依赖 transcript_version_id；setup 阶段跑 Import+Transcribe
    预建依赖（不计入测量），run 阶段只测 AnalyzeSource 本身。
    """

    def setup() -> tuple[Deps, str, str, str]:
        deps = _deps()
        ws_id, prj_id = _seed_project(deps)
        # 预建 asset + transcript
        r1 = asyncio.run(
            dispatch(
                _env(
                    "ImportSource",
                    {
                        "local_uri": "file:///tmp/perf/a.mp4",
                        "content_hash": f"hash-perf-a-{time.perf_counter_ns()}",
                        "kind": "video",
                    },
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
        )
        r2 = asyncio.run(
            dispatch(
                _env(
                    "TranscribeSource",
                    {"asset_id": r1["artifact_ids"][0], "opts": {"duration_sec": 6}},
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
        )
        return deps, ws_id, prj_id, r2["artifact_ids"][0]

    async def run(deps: Deps, ws_id: str, prj_id: str, transcript_id: str) -> None:
        res = await dispatch(
            _env(
                "AnalyzeSource",
                {"transcript_version_id": transcript_id},
                workspace_id=ws_id,
                project_id=prj_id,
            ),
            deps,
        )
        assert res["ok"], res

    median = _steady_median_ms(setup, run, name="AnalyzeSource")
    _assert_perf("AnalyzeSource", median)


@pytest.mark.perf
def test_perf_get_provenance() -> None:
    """GetProvenance 稳态耗时基线（回退 producer 路径）。"""

    def setup() -> tuple[Deps, str, str, str]:
        deps = _deps()
        ws_id, prj_id = _seed_project(deps)
        # 预建 analysis version（producer.kind=ai-analysis）
        r1 = asyncio.run(
            dispatch(
                _env(
                    "ImportSource",
                    {
                        "local_uri": "file:///tmp/perf/p.mp4",
                        "content_hash": f"hash-perf-p-{time.perf_counter_ns()}",
                        "kind": "video",
                    },
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
        )
        r2 = asyncio.run(
            dispatch(
                _env(
                    "TranscribeSource",
                    {"asset_id": r1["artifact_ids"][0], "opts": {"duration_sec": 6}},
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
        )
        r3 = asyncio.run(
            dispatch(
                _env(
                    "AnalyzeSource",
                    {"transcript_version_id": r2["artifact_ids"][0]},
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
        )
        return deps, ws_id, prj_id, r3["artifact_ids"][0]

    async def run(deps: Deps, ws_id: str, prj_id: str, analysis_id: str) -> None:
        res = await dispatch(
            _env(
                "GetProvenance",
                {"subjectType": "content_version", "subjectId": analysis_id},
                workspace_id=ws_id,
                project_id=prj_id,
            ),
            deps,
        )
        assert res["ok"], res

    median = _steady_median_ms(setup, run, name="GetProvenance")
    _assert_perf("GetProvenance", median)


@pytest.mark.perf
def test_perf_export_project() -> None:
    """ExportProject 稳态耗时基线（含 zip 打包）。"""
    import tempfile

    tmp_home = tempfile.mkdtemp(prefix="stepwork-perf-")
    os.environ["STEPWORK_HOME"] = tmp_home

    def setup() -> tuple[Deps, str, str, str]:
        deps = _deps()
        ws_id, prj_id = _seed_project(deps)
        # 预建 2 条 version 让导出有内容
        r1 = asyncio.run(
            dispatch(
                _env(
                    "ImportSource",
                    {
                        "local_uri": "file:///tmp/perf/e.mp4",
                        "content_hash": f"hash-perf-e-{time.perf_counter_ns()}",
                        "kind": "video",
                    },
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
        )
        asyncio.run(
            dispatch(
                _env(
                    "TranscribeSource",
                    {"asset_id": r1["artifact_ids"][0], "opts": {"duration_sec": 4}},
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
        )
        return deps, ws_id, prj_id, prj_id

    async def run(deps: Deps, ws_id: str, prj_id: str, target_prj_id: str) -> None:
        res = await dispatch(
            _env(
                "ExportProject",
                {"projectId": target_prj_id},
                workspace_id=ws_id,
                project_id=prj_id,
                command_id=f"cmd-perf-export-{time.perf_counter_ns()}",
            ),
            deps,
        )
        assert res["ok"], res

    try:
        median = _steady_median_ms(setup, run, name="ExportProject")
    finally:
        os.environ.pop("STEPWORK_HOME", None)
    _assert_perf("ExportProject", median)


# ---------------------------------------------------------------------------
# 全链路冷启动测量（一次 setup，跑完整链路）
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_perf_full_pipeline_cold_start() -> None:
    """全链路冷启动耗时基线：Import→Transcribe→Analyze→Topic→Script→Save→Render。

    每个 iteration 全新 setup（含 migrations + provider 构造），跑 7 条命令
    串行；重复 ``_STEADY_ITERS`` 次取中位数。结果记录到
    ``docs/PERF_BASELINE.md`` 作为「全链路冷启动」基线。
    """

    async def run_once(deps: Deps, ws_id: str, prj_id: str) -> None:
        t0 = time.perf_counter()
        try:
            r1 = await dispatch(
                _env(
                    "ImportSource",
                    {
                        "local_uri": "file:///tmp/perf/cold.mp4",
                        "content_hash": f"hash-perf-cold-{time.perf_counter_ns()}",
                        "kind": "video",
                    },
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
            assert r1["ok"], r1
            asset_id = r1["artifact_ids"][0]

            r2 = await dispatch(
                _env(
                    "TranscribeSource",
                    {"asset_id": asset_id, "opts": {"duration_sec": 12}},
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
            assert r2["ok"], r2
            transcript_id = r2["artifact_ids"][0]

            r3 = await dispatch(
                _env(
                    "AnalyzeSource",
                    {"transcript_version_id": transcript_id},
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
            assert r3["ok"], r3

            r4 = await dispatch(
                _env(
                    "GenerateTopic",
                    {"source_version_id": transcript_id, "count": 3},
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
            assert r4["ok"], r4
            proposal_id = r4["artifact_ids"][0]

            r5 = await dispatch(
                _env(
                    "GenerateScript",
                    {"proposal_version_id": proposal_id, "topic_id": "a1"},
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
            assert r5["ok"], r5
            script_id = r5["artifact_ids"][0]

            r6 = await dispatch(
                _env(
                    "SaveScript",
                    {
                        "content": json.dumps(
                            {"title": "冷启动稿", "body": "（0-3s）钩子\n（3-15s）正文"}
                        ),
                        "parent_version_id": script_id,
                    },
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
            assert r6["ok"], r6
            saved_id = r6["artifact_ids"][0]

            r7 = await dispatch(
                _env(
                    "CreateRenderJob",
                    {"source_version_id": saved_id, "tts_engine": "synthesize"},
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
            assert r7["ok"], r7
        finally:
            elapsed = (time.perf_counter() - t0) * 1000.0
            # 记录到 samples（finally 保证异常也记录，便于调试）
            run_once.last_elapsed = elapsed  # type: ignore[attr-defined]

    samples: list[float] = []
    for i in range(_WARMUP_ITERS + _STEADY_ITERS):
        deps = _deps()
        ws_id, prj_id = _seed_project(deps)
        asyncio.run(run_once(deps, ws_id, prj_id))
        if i >= _WARMUP_ITERS:
            samples.append(run_once.last_elapsed)  # type: ignore[attr-defined]

    median = statistics.median(samples)
    print(
        f"  [perf] FullPipelineColdStart: median={median:.2f}ms "
        f"(n={len(samples)})"
    )
    _assert_perf("FullPipelineColdStart", median)
