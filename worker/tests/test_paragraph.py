"""段落级编辑测试（PRD-SCR-003：段落级生成、重写、扩写和压缩）。

验收标准是「所有操作可撤销并生成版本」，因此重点锁死：
1. 四种操作都能跑通；
2. **只改目标段**，其余段落逐字不变；
3. 每次操作生成新版本且 parent 指向原版本（= 可撤销）。
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
from worker.runtime.script.paragraph import (
    join_paragraphs,
    replace_paragraph,
    split_paragraphs,
)

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"

_BODY = "第一段内容。\n第一段第二行。\n\n第二段内容。\n\n第三段内容。"


# ----- 纯函数：切分/拼接/替换 -----


def test_split_paragraphs_by_blank_line_keeps_inner_newlines() -> None:
    paras = split_paragraphs(_BODY)
    assert len(paras) == 3
    # 段内换行保留（口播脚本常见「一段多行」）
    assert paras[0] == "第一段内容。\n第一段第二行。"
    assert paras[1] == "第二段内容。"


def test_split_join_roundtrip() -> None:
    paras = split_paragraphs(_BODY)
    assert split_paragraphs(join_paragraphs(paras)) == paras


def test_split_ignores_empty_and_whitespace_blocks() -> None:
    assert split_paragraphs("") == []
    assert split_paragraphs("\n\n   \n\n") == []
    assert split_paragraphs("A\n\n\n\n\nB") == ["A", "B"]


def test_replace_paragraph_only_touches_target() -> None:
    out = replace_paragraph(_BODY, 1, "新的第二段")
    paras = split_paragraphs(out)
    assert paras[0] == "第一段内容。\n第一段第二行。"
    assert paras[1] == "新的第二段"
    assert paras[2] == "第三段内容。"


def test_replace_paragraph_empty_deletes() -> None:
    out = replace_paragraph(_BODY, 1, "   ")
    assert len(split_paragraphs(out)) == 2


# ----- 经 Command Bus 的端到端 -----


class _FakeAI:
    name = "fake-ai"
    model = "fake-1"
    estimated_cost_per_1k = 0.0

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.prompts.append(prompt)
        return {"text": "改写后的段落"}


def _setup(content: str = _BODY) -> tuple[Deps, str, str, str, _FakeAI]:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos = Repos(c)
    ws = repos.workspaces.insert(Workspace(name="ws-p", root_path="/tmp/p"))
    prj = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    cv = repos.content_versions.insert(
        ContentVersion(
            project_id=prj,
            content_type="script",
            content=content,
            content_hash="h",
        )
    )
    ai = _FakeAI()
    return Deps(repos=repos, ingest=ingest, ai=ai), ws, prj, cv, ai


def _env(payload: dict[str, Any], ws: str, prj: str) -> dict[str, Any]:
    return {
        "commandId": "cmd-p",
        "commandType": "EditParagraph",
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u"},
        "source": "ui",
        "workspaceId": ws,
        "projectId": prj,
        "payload": payload,
        "requestedAt": "2026-07-26T00:00:00+00:00",
    }


async def test_rewrite_replaces_only_target_paragraph() -> None:
    deps, ws, prj, cv, ai = _setup()
    res = await dispatch(
        _env({"version_id": cv, "paragraph_index": 1, "operation": "rewrite"}, ws, prj),
        deps,
    )
    assert res["ok"] is True, res.get("error")

    new_cv = deps.repos.content_versions.get(res["artifact_ids"][0])
    assert new_cv is not None
    paras = split_paragraphs(new_cv.content)
    assert paras[1] == "改写后的段落"
    # 其余段落逐字不变 —— 未选中的内容绝不能被模型改写
    assert paras[0] == "第一段内容。\n第一段第二行。"
    assert paras[2] == "第三段内容。"

    # prompt 带上下段上下文，且明确只要这一段
    prompt = ai.prompts[0]
    assert "第一段内容。" in prompt  # 上一段
    assert "第三段内容。" in prompt  # 下一段
    assert "只输出处理后的" in prompt


async def test_edit_creates_new_version_with_parent_link() -> None:
    """「所有操作可撤销」= 新版本 + parent 指向原版本。"""
    deps, ws, prj, cv, _ai = _setup()
    res = await dispatch(
        _env({"version_id": cv, "paragraph_index": 0, "operation": "condense"}, ws, prj),
        deps,
    )
    assert res["ok"] is True
    new_id = res["artifact_ids"][0]
    assert new_id != cv
    new_cv = deps.repos.content_versions.get(new_id)
    assert new_cv is not None
    assert new_cv.parent_version_id == cv
    assert new_cv.producer["kind"] == "ai-paragraph-edit"
    assert new_cv.producer["operation"] == "condense"
    # 原版本仍完整可读（撤销即回到它）
    original = deps.repos.content_versions.get(cv)
    assert original is not None
    assert original.content == _BODY


async def test_all_four_operations_supported() -> None:
    for op in ("rewrite", "expand", "condense", "generate"):
        deps, ws, prj, cv, _ai = _setup()
        res = await dispatch(
            _env({"version_id": cv, "paragraph_index": 0, "operation": op}, ws, prj),
            deps,
        )
        assert res["ok"] is True, f"{op}: {res.get('error')}"
        assert res["detail"]["operation"] == op


async def test_json_script_only_body_edited_title_preserved() -> None:
    """GenerateScript 产出的 {title, body} 形态：只改 body，标题不动。"""
    content = json.dumps({"title": "原标题", "body": _BODY}, ensure_ascii=False)
    deps, ws, prj, cv, _ai = _setup(content)
    res = await dispatch(
        _env({"version_id": cv, "paragraph_index": 1, "operation": "rewrite"}, ws, prj),
        deps,
    )
    assert res["ok"] is True
    new_cv = deps.repos.content_versions.get(res["artifact_ids"][0])
    assert new_cv is not None
    parsed = json.loads(new_cv.content)
    assert parsed["title"] == "原标题"
    assert "改写后的段落" in parsed["body"]


async def test_invalid_operation_and_index_rejected() -> None:
    deps, ws, prj, cv, _ai = _setup()

    bad_op = await dispatch(
        _env({"version_id": cv, "paragraph_index": 0, "operation": "translate"}, ws, prj),
        deps,
    )
    assert bad_op["ok"] is False
    assert "INVALID_ARGUMENT" in bad_op["error"]

    out_of_range = await dispatch(
        _env({"version_id": cv, "paragraph_index": 99, "operation": "rewrite"}, ws, prj),
        deps,
    )
    assert out_of_range["ok"] is False
    assert "out of range" in out_of_range["error"]

    missing = await dispatch(
        _env({"version_id": cv, "operation": "rewrite"}, ws, prj), deps
    )
    assert missing["ok"] is False
    assert "INVALID_ARGUMENT" in missing["error"]


async def test_edit_without_ai_provider_unavailable() -> None:
    deps, ws, prj, cv, _ai = _setup()
    deps.ai = None
    res = await dispatch(
        _env({"version_id": cv, "paragraph_index": 0, "operation": "rewrite"}, ws, prj),
        deps,
    )
    assert res["ok"] is False
    assert "UNAVAILABLE" in res["error"]
