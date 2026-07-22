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


def test_generate_topic() -> None:
    deps = _deps()
    src = _src_id(deps)
    res = dispatch(_env("GenerateTopic", {"source_version_id": src, "count": 3}), deps)
    assert res["ok"] is True
    assert len(res["artifact_ids"]) == 1
    row = deps.repos.conn.execute(
        "SELECT content_type, content FROM content_versions WHERE id=?",
        (res["artifact_ids"][0],),
    ).fetchone()
    assert row["content_type"] == "topic_proposal"
    data = json.loads(row["content"])
    assert len(data["angles"]) == 1  # fake 只给 1 个，截断到 count


def test_generate_script() -> None:
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
    res = dispatch(_env("GenerateScript", {"proposal_version_id": prop_id, "topic_id": "a1"}), deps)
    assert res["ok"] is True
    row = deps.repos.conn.execute(
        "SELECT content_type, parent_version_id FROM content_versions WHERE id=?",
        (res["artifact_ids"][0],),
    ).fetchone()
    assert row["content_type"] == "script"
    assert row["parent_version_id"] == prop_id


def test_save_script_version_chain() -> None:
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
    res = dispatch(
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
