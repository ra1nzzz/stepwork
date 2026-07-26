"""项目搜索/标签/最近访问测试（PRD-WS-002 / PRD-WS-003）。

此前 ListProjects 无任何查询参数，前端「搜索」只是对已拉取列表做本地
title includes；content_projects 无 tags 列、无最近访问时间，首页「最近
项目」实为 created_at 倒序；新建项目也不创建任何目录。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps


def _read_project_meta(project_dir: str) -> dict[str, Any] | None:
    """同步读 project.json（放在 async 测试外，避免 ruff ASYNC240）。"""
    meta_path = os.path.join(project_dir, "project.json")
    if not os.path.isfile(meta_path):
        return None
    with open(meta_path, encoding="utf-8") as f:
        loaded: dict[str, Any] = json.load(f)
        return loaded


_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _deps() -> Deps:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos = Repos(c)
    repos.workspaces.ensure("ws-q")
    return Deps(repos=repos, ingest=ingest)


def _env(
    command_type: str, payload: dict[str, Any] | None = None, prj: str | None = None
) -> dict[str, Any]:
    return {
        "commandId": "cmd-q",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u"},
        "source": "ui",
        "workspaceId": "ws-q",
        "projectId": prj,
        "payload": payload or {},
        "requestedAt": "2026-07-27T00:00:00+00:00",
    }


async def _create(deps: Deps, title: str, tags: list[str] | None = None) -> str:
    res = await dispatch(
        _env("CreateProject", {"title": title, "tags": tags or []}), deps
    )
    assert res["ok"] is True, res.get("error")
    return str(res["detail"]["project"]["id"])


# ----- PRD-WS-002：默认目录 -----


async def test_create_project_makes_default_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新建项目应创建默认目录并写 project.json（此前从不创建任何目录）。"""
    monkeypatch.setenv("STEPWORK_HOME", str(tmp_path))
    deps = _deps()
    res = await dispatch(_env("CreateProject", {"title": "带目录的项目"}), deps)
    assert res["ok"] is True
    project_dir = res["detail"]["project"]["project_dir"]
    assert project_dir, "应返回项目目录路径"
    meta = _read_project_meta(project_dir)
    assert meta is not None, f"项目目录未创建或缺 project.json：{project_dir}"
    assert meta["title"] == "带目录的项目"


# ----- PRD-WS-003：搜索 / 标签 / 最近访问 -----


async def test_list_projects_keyword_search() -> None:
    deps = _deps()
    await _create(deps, "自动化工作流实测")
    await _create(deps, "红烧肉教程")

    res = await dispatch(_env("ListProjects", {"keyword": "自动化"}), deps)
    assert res["ok"] is True
    titles = [p["title"] for p in res["detail"]["projects"]]
    assert titles == ["自动化工作流实测"]


async def test_list_projects_filter_by_tags() -> None:
    deps = _deps()
    await _create(deps, "A", ["效率", "AI"])
    await _create(deps, "B", ["美食"])
    await _create(deps, "C", ["AI"])

    res = await dispatch(_env("ListProjects", {"tags": ["AI"]}), deps)
    titles = {p["title"] for p in res["detail"]["projects"]}
    assert titles == {"A", "C"}

    # 多标签为 AND（同时含有）
    both = await dispatch(_env("ListProjects", {"tags": ["AI", "效率"]}), deps)
    assert {p["title"] for p in both["detail"]["projects"]} == {"A"}

    # 供 UI 渲染筛选器的全量标签
    assert set(res["detail"]["available_tags"]) >= {"AI", "效率", "美食"}


async def test_get_project_refreshes_last_accessed_and_sorts_recent() -> None:
    """最近访问必须真正生效：打开过的项目排到最前。"""
    deps = _deps()
    first = await _create(deps, "先建的")
    await _create(deps, "后建的")

    # 默认 recent 排序：刚建时按创建时间，后建的在前
    before = await dispatch(_env("ListProjects", {}), deps)
    assert before["detail"]["projects"][0]["title"] == "后建的"

    # 打开「先建的」→ 刷新访问时间
    got = await dispatch(_env("GetProject", {"projectId": first}), deps)
    assert got["ok"] is True
    assert got["detail"]["project"]["last_accessed_at"]

    after = await dispatch(_env("ListProjects", {}), deps)
    assert after["detail"]["projects"][0]["title"] == "先建的", (
        "最近访问过的项目应排在最前"
    )

    # created 排序不受访问影响
    by_created = await dispatch(_env("ListProjects", {"sort": "created"}), deps)
    assert by_created["detail"]["projects"][0]["title"] == "后建的"


async def test_set_project_tags_updates_and_validates() -> None:
    deps = _deps()
    pid = await _create(deps, "可改标签")

    res = await dispatch(
        _env("SetProjectTags", {"projectId": pid, "tags": ["新标签", "新标签", " "]}),
        deps,
    )
    assert res["ok"] is True
    # 去重 + 去空白
    assert res["detail"]["tags"] == ["新标签"]

    listed = await dispatch(_env("ListProjects", {}), deps)
    assert listed["detail"]["projects"][0]["tags"] == ["新标签"]


async def test_tag_validation_rejects_bad_input() -> None:
    deps = _deps()
    pid = await _create(deps, "校验")

    bad_type = await dispatch(
        _env("SetProjectTags", {"projectId": pid, "tags": "不是数组"}), deps
    )
    assert bad_type["ok"] is False
    assert "INVALID_ARGUMENT" in bad_type["error"]

    too_long = await dispatch(
        _env("SetProjectTags", {"projectId": pid, "tags": ["x" * 100]}), deps
    )
    assert too_long["ok"] is False
    assert "too long" in too_long["error"]

    too_many = await dispatch(
        _env(
            "SetProjectTags",
            {"projectId": pid, "tags": [f"t{i}" for i in range(50)]},
        ),
        deps,
    )
    assert too_many["ok"] is False
    assert "too many" in too_many["error"]


async def test_unknown_sort_rejected() -> None:
    deps = _deps()
    res = await dispatch(_env("ListProjects", {"sort": "random"}), deps)
    assert res["ok"] is False
    assert "unknown sort" in res["error"]
