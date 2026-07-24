"""W9 L.38 E2E 全链路测试。

验证 Import → Transcribe → Analyze → Topic → Script → SaveScript → Render
全链路在进程内 Command Bus 上顺畅流转，artifact 链路可追溯，GetProvenance
回退路径不崩溃。

设计决策（W9_PLAN D1）：走进程内 ``dispatch`` 而非真实 worker 子进程，
复用 in_memory DB + fake providers（LocalASR / LocalTTS / FakeFFmpeg /
_FakeAI），保证 CI windows-latest 无 sidecar 环境下可确定性运行。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

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


def _path_exists(path_str: str) -> bool:
    """同步 helper：检查路径存在性（避免 async 测试函数内触发 ASYNC240）。"""
    return Path(path_str).exists()


# AI provider 在三步返回不同形状（按 schema 区分）：
# - ANALYSIS_SCHEMA.title == "AnalysisReport" → 分析报告
# - TOPIC_SCHEMA.properties 有 "angles" → 选题 + 顺带 title/body（script 也能用）
# - SCRIPT_SCHEMA.properties 有 "title"/"body" 无 "angles" → 脚本
_ANALYSIS_VALID: dict[str, Any] = {
    "summary": "本期聊自动化工作流。",
    "topics": ["自动化", "工作流"],
    "sentiment": "positive",
    "suggested_title": "自动化工作流入门",
    "suggested_tags": ["自动化"],
    "key_points": ["导入素材", "AI 分析"],
    "target_audience": "创作者",
    "provider": "fake",
    "model": "fake-1",
    "confidence": 0.9,
}

_TOPIC_VALID: dict[str, Any] = {
    "angles": [
        {
            "id": "a1",
            "title": "三步搞定短视频",
            "rationale": "流程化降低门槛",
            "hook": "以为剪视频很难？三步就够",
        }
    ],
    # GenerateScript 复用同一 fake，顺带返回 title/body
    "title": "三步搞定短视频",
    "body": "（0-3s）钩子：以为剪视频很难？\n（3-10s）其实三步就够……\n（10-15s）关注我，下期拆解。",
}

_SCRIPT_VALID: dict[str, Any] = {
    "title": "三步搞定短视频",
    "body": "（0-3s）钩子：以为剪视频很难？\n（3-10s）其实三步就够……\n（10-15s）关注我，下期拆解。",
}


class _FakeAI:
    """根据 schema 形状返回不同 fixture 的确定性 AI Provider。"""

    name = "fake-ai"
    model = "fake-1"
    estimated_cost_per_1k = 0.0

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if schema and schema.get("title") == "AnalysisReport":
            return dict(_ANALYSIS_VALID)
        props = (schema or {}).get("properties", {}) if schema else {}
        if "angles" in props:
            return dict(_TOPIC_VALID)
        return dict(_SCRIPT_VALID)


def _deps() -> Deps:
    """构造全链路所需的依赖（in_memory DB + 全套 fake providers）。"""
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
    workspace_id: str = "ws-e2e",
    project_id: str | None = None,
) -> dict[str, Any]:
    return {
        "commandId": f"cmd-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": workspace_id,
        "projectId": project_id,
        "payload": payload,
        "requestedAt": "2026-07-23T00:00:00+00:00",
    }


def _seed_project(deps: Deps) -> tuple[str, str]:
    """预建 workspace + project，返 (workspace_id, project_id)。"""
    ws_id = deps.repos.workspaces.insert(Workspace(name="ws-e2e", root_path="/tmp/e2e"))
    prj_id = deps.repos.projects.insert(
        ContentProject(workspace_id=ws_id, title="E2E 全链路项目")
    )
    return ws_id, prj_id


async def test_full_pipeline_import_to_render() -> None:
    """全链路：Import → Transcribe → Analyze → Topic → Script → Save → Render。

    断言：
    - 每步 ok=True
    - artifact_ids 链路可追溯（上一步输出作为下一步输入）
    - content_versions 表最终含 6 条（transcript/analysis/topic_proposal/
      script/script-saved/video_draft）
    - jobs 表至少 4 条（transcribe/analyze/topic/script/render）
    """
    deps = _deps()
    ws_id, prj_id = _seed_project(deps)

    # 1. ImportSource
    r1 = await dispatch(
        _env(
            "ImportSource",
            {
                "local_uri": "file:///tmp/e2e/clip.mp4",
                "content_hash": "hash-e2e-1",
                "kind": "video",
            },
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert r1["ok"] is True, r1
    asset_id = r1["artifact_ids"][0]
    assert asset_id, "ImportSource should produce an asset id"

    # 2. TranscribeSource（用 asset_id 串接链路）
    r2 = await dispatch(
        _env(
            "TranscribeSource",
            {"asset_id": asset_id, "opts": {"duration_sec": 12}},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert r2["ok"] is True, r2
    transcript_id = r2["artifact_ids"][0]
    assert transcript_id, "TranscribeSource should produce a transcript version id"

    # 3. AnalyzeSource（输入 transcript_version_id）
    r3 = await dispatch(
        _env(
            "AnalyzeSource",
            {"transcript_version_id": transcript_id},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert r3["ok"] is True, r3
    analysis_id = r3["artifact_ids"][0]
    assert analysis_id != transcript_id, "analysis must be a new version"

    # 4. GenerateTopic（输入 transcript_version_id）
    r4 = await dispatch(
        _env(
            "GenerateTopic",
            {"source_version_id": transcript_id, "count": 3},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert r4["ok"] is True, r4
    proposal_id = r4["artifact_ids"][0]

    # 5. GenerateScript（输入 proposal_version_id + topic_id）
    r5 = await dispatch(
        _env(
            "GenerateScript",
            {"proposal_version_id": proposal_id, "topic_id": "a1"},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert r5["ok"] is True, r5
    script_id = r5["artifact_ids"][0]

    # 6. SaveScript（输入 content + parent_version_id=script_id）
    saved_content = json.dumps(
        {"title": "E2E 保存稿", "body": "（0-3s）钩子\n（3-15s）正文\n（15s）收尾"}
    )
    r6 = await dispatch(
        _env(
            "SaveScript",
            {"content": saved_content, "parent_version_id": script_id},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert r6["ok"] is True, r6
    saved_id = r6["artifact_ids"][0]

    # 7. CreateRenderJob（输入 source_version_id=saved_id）
    r7 = await dispatch(
        _env(
            "CreateRenderJob",
            {"source_version_id": saved_id, "tts_engine": "synthesize"},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert r7["ok"] is True, r7
    assert r7["job_id"], "CreateRenderJob should produce a job_id"

    # 断言：content_versions 链路完整（6 条）
    rows = deps.repos.conn.execute(
        "SELECT id, content_type, parent_version_id FROM content_versions "
        "WHERE project_id=? ORDER BY created_at",
        (prj_id,),
    ).fetchall()
    types = [str(r["content_type"]) for r in rows]
    assert "transcript" in types
    assert "analysis" in types
    assert "topic_proposal" in types
    assert "script" in types  # 至少一条 script（GenerateScript 产的）
    assert "video_draft" in types
    assert len(rows) >= 6, f"expected >=6 versions, got {len(rows)}: {types}"

    # 断言：版本链 parent 关系正确（GenerateScript 的 parent 是 proposal）
    script_row = deps.repos.conn.execute(
        "SELECT parent_version_id FROM content_versions WHERE id=?", (script_id,)
    ).fetchone()
    assert script_row is not None
    assert str(script_row["parent_version_id"]) == proposal_id

    # 断言：jobs 表至少 4 条（transcribe/analyze/topic/script/render = 5）
    job_count = deps.repos.conn.execute(
        "SELECT COUNT(*) FROM jobs"
    ).fetchone()[0]
    assert job_count >= 5, f"expected >=5 jobs, got {job_count}"


async def test_e2e_provenance_fallback_after_pipeline() -> None:
    """E2E 跑完后，GetProvenance 对 analysis version 回退 producer 不崩溃。

    W8 D2：W8 只读聚合，provenance_records 表为空时回退 content_versions.producer。
    E2E 验证全链路产物在无 provenance_records 时可被 GetProvenance 聚合。
    """
    deps = _deps()
    ws_id, prj_id = _seed_project(deps)

    # 跑到 AnalyzeSource 拿到 analysis_id（producer.kind="ai-analysis"）
    asset_res = await dispatch(
        _env(
            "ImportSource",
            {"local_uri": "file:///tmp/e2e/p.mp4", "content_hash": "h-p", "kind": "video"},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    asset_id = asset_res["artifact_ids"][0]

    trans_res = await dispatch(
        _env(
            "TranscribeSource",
            {"asset_id": asset_id, "opts": {"duration_sec": 8}},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    transcript_id = trans_res["artifact_ids"][0]

    ana_res = await dispatch(
        _env(
            "AnalyzeSource",
            {"transcript_version_id": transcript_id},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    analysis_id = ana_res["artifact_ids"][0]

    # GetProvenance 回退路径
    prov = await dispatch(
        _env(
            "GetProvenance",
            {"subjectType": "content_version", "subjectId": analysis_id},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert prov["ok"] is True, prov
    # 回退 producer 时 ai_label_state 应为 "ai-generated"（producer.kind="ai-analysis"）
    provenance = prov["detail"].get("provenance", {})
    state = provenance.get("ai_label_state")
    assert state == "ai-generated", f"expected ai-generated, got {state!r}"


async def test_e2e_export_import_after_pipeline() -> None:
    """E2E 跑完后，ExportProject → ImportProject 往返一致（W9 L.39 集成验证）。

    验证：全链路产物可被导出为 zip，再导入到新 workspace 后元数据一致。
    """
    import tempfile

    deps = _deps()
    ws_id, prj_id = _seed_project(deps)

    # 跑 Import + Transcribe + Analyze
    asset_res = await dispatch(
        _env(
            "ImportSource",
            {"local_uri": "file:///tmp/e2e/ei.mp4", "content_hash": "h-ei", "kind": "video"},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    asset_id = asset_res["artifact_ids"][0]

    trans_res = await dispatch(
        _env(
            "TranscribeSource",
            {"asset_id": asset_id, "opts": {"duration_sec": 6}},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    transcript_id = trans_res["artifact_ids"][0]

    await dispatch(
        _env(
            "AnalyzeSource",
            {"transcript_version_id": transcript_id},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )

    # 导出（STEPWORK_HOME 指向临时目录避免污染）
    with tempfile.TemporaryDirectory() as tmp_home:
        os.environ["STEPWORK_HOME"] = tmp_home
        try:
            exp = await dispatch(
                _env(
                    "ExportProject",
                    {"projectId": prj_id},
                    workspace_id=ws_id,
                    project_id=prj_id,
                ),
                deps,
            )
            assert exp["ok"] is True, exp
            bundle_path = exp["detail"]["bundle_path"]
            assert _path_exists(bundle_path)
            assert exp["detail"]["versions_count"] >= 2  # transcript + analysis

            # 导入到新 workspace
            imp = await dispatch(
                _env(
                    "ImportProject",
                    {"bundlePath": bundle_path, "remapId": True},
                    workspace_id="ws-import-target",
                ),
                deps,
            )
            assert imp["ok"] is True, imp
            new_prj_id = imp["detail"]["project_id"]
            assert new_prj_id != prj_id, "remapId should produce a new project id"
            assert imp["detail"]["imported_versions"] >= 2
            assert imp["detail"]["imported_assets"] >= 1
        finally:
            os.environ.pop("STEPWORK_HOME", None)
