"""相似度提醒测试（PRD-SCR-004 历史选题重复 / PRD-SCR-005 原创性）。

设置页的 ``check-similarity`` 此前是空开关（能勾、无行为）。这里既覆盖
算法本身，也覆盖「生成时真的会给出提醒」这条端到端链路。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentProject, ContentVersion, Workspace
from worker.runtime.script.history import load_script_history, load_topic_history
from worker.runtime.script.similarity import (
    find_similar,
    hits_to_warnings,
    jaccard,
    ngrams,
    normalize,
)

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


# ----- 算法 -----


def test_normalize_strips_punctuation_and_case() -> None:
    assert normalize("Hello, 世界！") == "hello世界"
    assert normalize("  A  B  ") == "ab"


def test_ngrams_short_text_degrades_to_whole() -> None:
    assert ngrams("ab") == {"ab"}
    assert "自动化" in ngrams("自动化工作流")


def test_jaccard_identical_and_disjoint() -> None:
    assert jaccard("完全一样的文本", "完全一样的文本") == 1.0
    assert jaccard("自动化工作流", "红烧肉的做法") == 0.0
    # 空文本不炸
    assert jaccard("", "任意") == 0.0


def test_jaccard_partial_overlap_between_0_and_1() -> None:
    score = jaccard(
        "今天我们聊聊自动化工作流的入门",
        "今天我们聊聊自动化工作流的进阶",
    )
    assert 0.0 < score < 1.0
    # 高度重合应明显高于阈值
    assert score > 0.5


def test_find_similar_respects_threshold_and_sorts() -> None:
    candidates = [
        {"id": "a", "text": "自动化工作流入门指南", "label": "A"},
        {"id": "b", "text": "完全无关的红烧肉教程", "label": "B"},
        {"id": "c", "text": "自动化工作流入门", "label": "C"},
    ]
    hits = find_similar("自动化工作流入门指南", candidates, threshold=0.3)
    ids = [h.ref_id for h in hits]
    assert "b" not in ids, "低于阈值的不该命中"
    assert ids[0] == "a", "应按相似度降序"
    assert all(hits[i].score >= hits[i + 1].score for i in range(len(hits) - 1))


def test_hits_to_warnings_wording_is_advisory() -> None:
    hits = find_similar(
        "自动化工作流", [{"id": "x", "text": "自动化工作流", "label": "旧选题"}]
    )
    warnings = hits_to_warnings(hits, "duplicate_topic")
    assert warnings[0]["kind"] == "duplicate_topic"
    # PRD-SCR-005 要求「不做法律结论」：措辞是请确认，不是判定抄袭
    assert "请确认" in warnings[0]["message"]
    assert "抄袭" not in warnings[0]["message"]


# ----- 历史检索 -----


def _seed(conn: Any) -> tuple[Repos, str, str]:
    repos = Repos(conn)
    ws = repos.workspaces.insert(Workspace(name="ws-s", root_path="/tmp/s"))
    prj = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    return repos, ws, prj


def test_load_topic_history_flattens_angles() -> None:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos, _ws, prj = _seed(c)
    repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            content_type="topic_proposal",
            content=json.dumps(
                {"angles": [{"id": "a1", "title": "旧角度", "rationale": "理由"}]}
            ),
            content_hash="h",
        )
    )
    history = load_topic_history(c, prj)
    assert len(history) == 1
    assert history[0]["label"] == "旧角度"
    # 标题 + 依据一起比，避免只看标题误判
    assert "理由" in history[0]["text"]


def test_load_script_history_handles_both_shapes() -> None:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos, _ws, prj = _seed(c)
    repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            content_type="script",
            content=json.dumps({"title": "T1", "body": "生成路径正文"}),
            content_hash="h1",
        )
    )
    repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            content_type="script",
            content=json.dumps({"text": "编辑器路径正文", "title": "T2"}),
            content_hash="h2",
        )
    )
    history = load_script_history(c, prj)
    texts = {h["text"] for h in history}
    assert "生成路径正文" in texts
    assert "编辑器路径正文" in texts, "编辑器保存的 {text,title} 形态不能漏"


def test_topic_history_spans_projects_of_same_brand() -> None:
    """PRD-SCR-004 要求按「项目和账号历史」提醒，故跨同 BrandProfile 项目。"""
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos, ws, prj_a = _seed(c)
    profile_id = "bp_shared"
    c.execute(
        "INSERT INTO brand_profiles (id, workspace_id, name, positioning, audience, "
        "tone, content_pillars, banned_expressions, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (profile_id, ws, "品牌", "", "", "", "[]", "[]", "t", "t"),
    )
    prj_b = repos.projects.insert(ContentProject(workspace_id=ws, title="另一个项目"))
    for pid in (prj_a, prj_b):
        c.execute(
            "UPDATE content_projects SET brand_profile_id=? WHERE id=?",
            (profile_id, pid),
        )
    c.commit()
    repos.content_versions.insert(
        ContentVersion(
            project_id=prj_b,
            content_type="topic_proposal",
            content=json.dumps({"angles": [{"id": "x", "title": "隔壁项目的选题"}]}),
            content_hash="h",
        )
    )

    history = load_topic_history(c, prj_a)
    assert any(h["label"] == "隔壁项目的选题" for h in history), (
        "同账号其它项目的历史选题应纳入比对"
    )


# ----- 端到端：生成时给出提醒 -----


class _FixedAI:
    name = "fixed-ai"
    model = "fixed-1"
    estimated_cost_per_1k = 0.0

    def __init__(self, angle_title: str, body: str) -> None:
        self.angle_title = angle_title
        self.body = body

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        props = (schema or {}).get("properties", {}) if schema else {}
        if "angles" in props:
            return {
                "angles": [
                    {
                        "id": f"a{i}",
                        "title": self.angle_title,
                        "rationale": "同样的理由",
                        "hook": "钩子",
                        "audience": "受众",
                        "stance": "观点",
                        "risks": [],
                    }
                    for i in range(1, 4)
                ]
            }
        return {"title": "标题", "body": self.body}


def _env(command_type: str, payload: dict[str, Any], ws: str, prj: str) -> dict[str, Any]:
    return {
        "commandId": "cmd-sim",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u"},
        "source": "ui",
        "workspaceId": ws,
        "projectId": prj,
        "payload": payload,
        "requestedAt": "2026-07-26T00:00:00+00:00",
    }


async def test_generate_topic_warns_on_duplicate_history() -> None:
    """PRD-SCR-004 端到端：与历史选题高度相似时 detail 给出提醒。"""
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos, ws, prj = _seed(c)
    src = repos.content_versions.insert(
        ContentVersion(
            project_id=prj, content_type="transcript", content="素材", content_hash="h0"
        )
    )
    repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            content_type="topic_proposal",
            content=json.dumps(
                {"angles": [{"id": "old", "title": "自动化工作流入门", "rationale": "同样的理由"}]}
            ),
            content_hash="h1",
        )
    )
    deps = Deps(
        repos=repos, ingest=ingest, ai=_FixedAI("自动化工作流入门", "正文")
    )

    res = await dispatch(
        _env("GenerateTopic", {"source_version_id": src, "count": 3}, ws, prj), deps
    )
    assert res["ok"] is True, res.get("error")
    warnings = res["detail"]["duplicate_warnings"]
    assert warnings, "与历史几乎相同的选题必须给出重复提醒"
    assert warnings[0]["kind"] == "duplicate_topic"
    assert warnings[0]["score"] >= 0.5


async def test_generate_topic_no_warning_when_novel() -> None:
    """全新选题不应产生噪声提醒。"""
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos, ws, prj = _seed(c)
    src = repos.content_versions.insert(
        ContentVersion(
            project_id=prj, content_type="transcript", content="素材", content_hash="h0"
        )
    )
    repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            content_type="topic_proposal",
            content=json.dumps({"angles": [{"id": "old", "title": "红烧肉的家常做法"}]}),
            content_hash="h1",
        )
    )
    deps = Deps(repos=repos, ingest=ingest, ai=_FixedAI("量子计算入门科普", "正文"))

    res = await dispatch(
        _env("GenerateTopic", {"source_version_id": src, "count": 3}, ws, prj), deps
    )
    assert res["ok"] is True
    assert res["detail"]["duplicate_warnings"] == []
