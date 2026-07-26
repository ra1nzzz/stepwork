"""版本比较测试（PRD-SCR-006「可比较 AI 初稿和最终稿」）。

版本链此前已完备，但没有比较能力——用户只能各自打开两个版本肉眼比对，
也没有「AI 初稿」这个锚点。
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
from worker.runtime.script.diff import diff_lines, extract_text, summarize

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


# ----- 纯函数 -----


def test_extract_text_handles_all_three_shapes() -> None:
    assert extract_text(json.dumps({"title": "T", "body": "B"})) == ("B", "T")
    assert extract_text(json.dumps({"text": "X", "title": "T2"})) == ("X", "T2")
    assert extract_text("裸文本") == ("裸文本", "")


def test_diff_marks_insert_delete_and_equal() -> None:
    lines = diff_lines("A\nB\nC", "A\nB2\nC\nD")
    ops = [line["op"] for line in lines]
    assert "equal" in ops
    assert "delete" in ops
    assert "insert" in ops
    stats = summarize(lines)
    assert stats["unchanged"] == 2  # A 与 C
    assert stats["removed"] == 1  # B
    assert stats["added"] == 2  # B2 与 D


def test_diff_identical_has_no_changes() -> None:
    lines = diff_lines("同样的内容\n第二行", "同样的内容\n第二行")
    stats = summarize(lines)
    assert stats["added"] == 0
    assert stats["removed"] == 0
    assert stats["unchanged"] == 2


def test_diff_line_numbers_present() -> None:
    lines = diff_lines("A", "B")
    deleted = next(line for line in lines if line["op"] == "delete")
    inserted = next(line for line in lines if line["op"] == "insert")
    assert deleted["before_line"] == 1
    assert deleted["after_line"] is None
    assert inserted["after_line"] == 1
    assert inserted["before_line"] is None


# ----- 端到端 -----


def _setup() -> tuple[Deps, str, str]:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos = Repos(c)
    ws = repos.workspaces.insert(Workspace(name="ws-d", root_path="/tmp/d"))
    prj = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    return Deps(repos=repos, ingest=ingest), ws, prj


def _env(payload: dict[str, Any], ws: str, prj: str) -> dict[str, Any]:
    return {
        "commandId": "cmd-d",
        "commandType": "DiffContentVersions",
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u"},
        "source": "ui",
        "workspaceId": ws,
        "projectId": prj,
        "payload": payload,
        "requestedAt": "2026-07-27T00:00:00+00:00",
    }


def _chain(repos: Repos, prj: str) -> tuple[str, str, str]:
    """构造 AI 初稿 → 用户改写 → 再改写 的版本链。"""
    ai = repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            content_type="script",
            content=json.dumps({"title": "初稿", "body": "第一行\n第二行"}),
            content_hash="h1",
            producer={"kind": "ai-script"},
        )
    )
    mid = repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            parent_version_id=ai,
            content_type="script",
            content=json.dumps({"text": "第一行\n改过的第二行", "title": "中间"}),
            content_hash="h2",
            producer={"kind": "user-script"},
        )
    )
    final = repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            parent_version_id=mid,
            content_type="script",
            content=json.dumps({"text": "第一行\n改过的第二行\n新增第三行", "title": "终稿"}),
            content_hash="h3",
            producer={"kind": "user-script"},
        )
    )
    return ai, mid, final


async def test_diff_auto_anchors_to_ai_draft() -> None:
    """不给 base 时，自动沿 parent 链上溯定位 AI 初稿（PRD-SCR-006 锚点）。"""
    deps, ws, prj = _setup()
    ai, _mid, final = _chain(deps.repos, prj)

    res = await dispatch(_env({"versionId": final}, ws, prj), deps)
    assert res["ok"] is True, res.get("error")
    assert res["detail"]["base_version_id"] == ai
    assert res["detail"]["base_is_ai_draft"] is True
    assert res["detail"]["base_title"] == "初稿"
    assert res["detail"]["target_title"] == "终稿"

    stats = res["detail"]["summary"]
    assert stats["added"] >= 2  # 改过的第二行 + 新增第三行
    assert stats["removed"] >= 1  # 原第二行


async def test_diff_with_explicit_base() -> None:
    deps, ws, prj = _setup()
    _ai, mid, final = _chain(deps.repos, prj)
    res = await dispatch(
        _env({"versionId": final, "baseVersionId": mid}, ws, prj), deps
    )
    assert res["ok"] is True
    assert res["detail"]["base_version_id"] == mid
    assert res["detail"]["base_is_ai_draft"] is False
    # 与相邻版本只差新增一行
    assert res["detail"]["summary"]["added"] == 1
    assert res["detail"]["summary"]["removed"] == 0


async def test_diff_falls_back_to_parent_when_no_ai_draft() -> None:
    """全程手写（无 AI 初稿）时回落到 parent 版本。"""
    deps, ws, prj = _setup()
    first = deps.repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            content_type="script",
            content="手写第一版",
            content_hash="m1",
            producer={"kind": "user-script"},
        )
    )
    second = deps.repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            parent_version_id=first,
            content_type="script",
            content="手写第二版",
            content_hash="m2",
            producer={"kind": "user-script"},
        )
    )
    res = await dispatch(_env({"versionId": second}, ws, prj), deps)
    assert res["ok"] is True
    assert res["detail"]["base_version_id"] == first
    assert res["detail"]["base_is_ai_draft"] is False


async def test_diff_rejects_missing_and_unknown_versions() -> None:
    deps, ws, prj = _setup()
    missing = await dispatch(_env({}, ws, prj), deps)
    assert missing["ok"] is False
    assert "INVALID_ARGUMENT" in missing["error"]

    unknown = await dispatch(_env({"versionId": "nope"}, ws, prj), deps)
    assert unknown["ok"] is False
    assert "NOT_FOUND" in unknown["error"]


async def test_diff_without_base_or_parent_rejected() -> None:
    """孤版本（无 AI 初稿也无 parent）应给出清晰错误而不是空 diff。"""
    deps, ws, prj = _setup()
    lone = deps.repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            content_type="script",
            content="孤版本",
            content_hash="lone",
            producer={"kind": "user-script"},
        )
    )
    res = await dispatch(_env({"versionId": lone}, ws, prj), deps)
    assert res["ok"] is False
    assert "no base version" in res["error"]
