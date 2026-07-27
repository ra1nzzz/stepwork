"""W5 Topic/Script 生成 + 自动保存（版本链）测试。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentVersion

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


class _FakeAI:
    name = "fake-ai"
    model = "fake-model"

    async def complete(self, prompt: str, schema: Any = None) -> dict[str, Any]:
        return {
            "angles": [
                {"id": "a1", "title": "角度一", "rationale": "差异化", "hook": "钩子"},
            ],
            "title": "脚本标题",
            "body": "脚本正文",
        }


def _deps() -> Deps:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    return Deps(repos=Repos(c), ai=_FakeAI())


def _env(command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "commandId": "cmd-t",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": "ws-t",
        "payload": payload,
        "requestedAt": "2026-07-22T00:00:00+00:00",
    }


def _pid(deps: Deps) -> str:
    """确保 workspace 存在后再取默认 project（handler 流程要求）。"""
    deps.repos.workspaces.ensure("ws-t")
    return deps.repos.projects.get_or_create_default("ws-t").id


def _src_id(deps: Deps) -> str:
    pid = _pid(deps)
    return deps.repos.content_versions.insert(
        ContentVersion(
            project_id=pid,
            content_type="transcript",
            content="原始素材文本",
            content_hash="h0",
            producer={},
        )
    )


async def test_generate_topic() -> None:
    deps = _deps()
    src = _src_id(deps)
    res = await dispatch(_env("GenerateTopic", {"source_version_id": src, "count": 3}), deps)
    assert res["ok"] is True
    assert len(res["artifact_ids"]) == 1
    row = deps.repos.conn.execute(
        "SELECT content_type, content FROM content_versions WHERE id=?",
        (res["artifact_ids"][0],),
    ).fetchone()
    assert row["content_type"] == "topic_proposal"
    data = json.loads(row["content"])
    assert len(data["angles"]) == 1  # fake 只给 1 个，截断到 count


async def test_generate_script() -> None:
    deps = _deps()
    pid = _pid(deps)
    prop_id = deps.repos.content_versions.insert(
        ContentVersion(
            project_id=pid,
            content_type="topic_proposal",
            content=json.dumps(
                {"angles": [{"id": "a1", "title": "t", "rationale": "r", "hook": "h"}]}
            ),
            content_hash="hp",
            producer={},
        )
    )
    res = await dispatch(
        _env("GenerateScript", {"proposal_version_id": prop_id, "topic_id": "a1"}),
        deps,
    )
    assert res["ok"] is True
    row = deps.repos.conn.execute(
        "SELECT content_type, parent_version_id FROM content_versions WHERE id=?",
        (res["artifact_ids"][0],),
    ).fetchone()
    assert row["content_type"] == "script"
    assert row["parent_version_id"] == prop_id


async def test_save_script_version_chain() -> None:
    deps = _deps()
    pid = _pid(deps)
    v1 = deps.repos.content_versions.insert(
        ContentVersion(
            project_id=pid,
            content_type="script",
            content=json.dumps({"title": "v1", "body": "b1"}),
            content_hash="v1h",
            producer={},
        )
    )
    res = await dispatch(
        _env(
            "SaveScript",
            {"content": json.dumps({"title": "v2", "body": "b2"}), "parent_version_id": v1},
        ),
        deps,
    )
    assert res["ok"] is True
    v2 = res["artifact_ids"][0]
    row = deps.repos.conn.execute(
        "SELECT parent_version_id FROM content_versions WHERE id=?", (v2,)
    ).fetchone()
    assert row["parent_version_id"] == v1


# ----- PRD-SCR-001：角度须含受众/观点/差异/风险 + 3—5 个约束 -----


class _RichAI:
    """返回 PRD-SCR-001 全字段角度的假 AI。"""

    name = "rich-ai"
    model = "rich-1"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, prompt: str, schema: Any = None) -> dict[str, Any]:
        self.prompts.append(prompt)
        return {
            "angles": [
                {
                    "id": f"a{i}",
                    "title": f"角度{i}",
                    "rationale": "差异化依据",
                    "hook": "钩子",
                    "audience": "效率工具用户",
                    "stance": "自动化优先于手工",
                    "risks": ["样本有限"],
                }
                for i in range(1, 4)
            ],
            "title": "t",
            "body": "b",
        }


def _seed_source(deps: Deps) -> str:
    deps.repos.workspaces.ensure("ws-t")
    prj = deps.repos.projects.get_or_create_default("ws-t").id
    return deps.repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            content_type="transcript",
            content="素材文本",
            content_hash="h",
        )
    )


async def test_topic_angles_carry_audience_stance_risks() -> None:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    ai = _RichAI()
    deps = Deps(repos=Repos(c), ai=ai)
    cv = _seed_source(deps)

    res = await dispatch(
        _env("GenerateTopic", {"source_version_id": cv, "count": 3}), deps
    )
    assert res["ok"] is True, res.get("error")

    stored = deps.repos.content_versions.get(res["artifact_ids"][0])
    assert stored is not None
    angles = json.loads(stored.content)["angles"]
    assert len(angles) == 3
    # PRD-SCR-001 四要素齐备
    for a in angles:
        assert a["audience"]
        assert a["stance"]
        assert isinstance(a["risks"], list)
        assert a["rationale"]

    # prompt 明确索取这三项，否则模型不会产出
    assert "audience" in ai.prompts[0]
    assert "stance" in ai.prompts[0]
    assert "risks" in ai.prompts[0]


async def test_topic_count_must_be_between_3_and_5() -> None:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    deps = Deps(repos=Repos(c), ai=_RichAI())
    cv = _seed_source(deps)

    for bad in (1, 2, 6, 100):
        res = await dispatch(
            _env("GenerateTopic", {"source_version_id": cv, "count": bad}), deps
        )
        assert res["ok"] is False, f"count={bad} 应被拒绝"
        assert "INVALID_ARGUMENT" in res["error"]

    for good in (3, 4, 5):
        res = await dispatch(
            _env("GenerateTopic", {"source_version_id": cv, "count": good}), deps
        )
        assert res["ok"] is True, f"count={good} 应被接受"
