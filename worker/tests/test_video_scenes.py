"""``video_scenes`` 分幕命令测试（S2 地基）。

``migrations/0012`` 只是把表建出来；本文件保证这张表**真的有代码读写**——
本仓已经有 `core/*`、`sdk/*`、`publisher-engine/*` 一堆只有 `.gitkeep` 的
空壳目录了，别再多一张没人碰的空表。

覆盖：存档 / 列出 / 回填 / 替换语义 / 隐性规则断言（highlight 必须是 text
子串）/ 空列表与重复 seq 拦截 / Agent 白名单（读放行、写降级为审批）。
"""

from __future__ import annotations

from typing import Any

import pytest

from worker.runtime.bootstrap import migrations_dir
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import (
    CommandEnvelope,
    ContentProject,
    ContentVersion,
    Workspace,
)


def _env(
    command_type: str, payload: dict[str, Any], project_id: str, **over: Any
) -> CommandEnvelope:
    actor = over.pop("actor", {"type": "user", "id": "u1"})
    source = over.pop("source", "ui")
    return CommandEnvelope(
        commandId="cmd-1",
        commandType=command_type,
        actor=actor,
        source=source,
        workspaceId="ws-x",
        projectId=project_id,
        payload=payload,
        requestedAt="2026-09-09T00:00:00Z",
    )


def _seed() -> tuple[Any, Deps, str, str]:
    conn = in_memory()
    run_migrations(conn, migrations_dir())
    repos = Repos(conn)
    ws = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj_id = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    cv_id = repos.content_versions.insert(
        ContentVersion(
            project_id=prj_id,
            content_type="script",
            content="第一幕。第二幕。",
            content_hash="h",
            producer={},
        )
    )
    return conn, Deps(repos=repos), prj_id, cv_id


SCENES = [
    {"seq": 0, "text": "今天我想讲一个观点。", "emotion": "calm", "highlight": "一个观点"},
    {"seq": 1, "text": "性别对立的本质，是弱者互害。", "highlight": "弱者互害"},
]


async def test_save_and_list_roundtrip() -> None:
    conn, deps, prj_id, cv_id = _seed()
    out = await dispatch(
        _env("SaveVideoScenes", {"versionId": cv_id, "scenes": SCENES}, prj_id).model_dump(),
        deps,
    )
    assert out["ok"] is True, out
    assert out["detail"]["count"] == 2
    assert out["detail"]["replaced"] is True

    listed = await dispatch(
        _env("ListVideoScenes", {"versionId": cv_id}, prj_id).model_dump(), deps
    )
    assert listed["ok"] is True, listed
    scenes = listed["detail"]["scenes"]
    assert [s["seq"] for s in scenes] == [0, 1]  # 按 seq 升序
    assert scenes[0]["text"] == "今天我想讲一个观点。"
    assert scenes[0]["emotion"] == "calm"
    assert scenes[0]["duration_sec"] == 0.0  # TTS 还没跑


async def test_save_replaces_previous_scenes() -> None:
    """重新分幕是覆盖，不是追加（否则旧幕会混进成片）。"""
    conn, deps, prj_id, cv_id = _seed()
    await dispatch(
        _env("SaveVideoScenes", {"versionId": cv_id, "scenes": SCENES}, prj_id).model_dump(),
        deps,
    )
    await dispatch(
        _env(
            "SaveVideoScenes",
            {"versionId": cv_id, "scenes": [{"seq": 0, "text": "全新的一幕"}]},
            prj_id,
        ).model_dump(),
        deps,
    )
    listed = await dispatch(
        _env("ListVideoScenes", {"versionId": cv_id}, prj_id).model_dump(), deps
    )
    assert listed["detail"]["count"] == 1
    assert listed["detail"]["scenes"][0]["text"] == "全新的一幕"


async def test_save_rejects_highlight_not_substring() -> None:
    """隐性规则固化：highlight 非 text 子串 → 标红静默失效，入口必须拦。"""
    conn, deps, prj_id, cv_id = _seed()
    out = await dispatch(
        _env(
            "SaveVideoScenes",
            {
                "versionId": cv_id,
                "scenes": [{"seq": 0, "text": "原文在这里", "highlight": "别处的高亮"}],
            },
            prj_id,
        ).model_dump(),
        deps,
    )
    assert out["ok"] is False
    assert "INVALID_ARGUMENT" in out["error"]
    assert "substring" in out["error"]


async def test_save_rejects_empty_scenes() -> None:
    """空列表 = 删光所有幕，语义太危险 → 直接拒绝。"""
    conn, deps, prj_id, cv_id = _seed()
    out = await dispatch(
        _env("SaveVideoScenes", {"versionId": cv_id, "scenes": []}, prj_id).model_dump(),
        deps,
    )
    assert out["ok"] is False
    assert "INVALID_ARGUMENT" in out["error"]


async def test_save_rejects_duplicate_seq() -> None:
    """同批 seq 重复要早拦，否则只会得到一个裸 IntegrityError。"""
    conn, deps, prj_id, cv_id = _seed()
    out = await dispatch(
        _env(
            "SaveVideoScenes",
            {
                "versionId": cv_id,
                "scenes": [
                    {"seq": 0, "text": "甲"},
                    {"seq": 0, "text": "乙"},
                ],
            },
            prj_id,
        ).model_dump(),
        deps,
    )
    assert out["ok"] is False
    assert "duplicate seq" in out["error"]


async def test_save_rejects_unknown_version() -> None:
    conn, deps, prj_id, cv_id = _seed()
    out = await dispatch(
        _env("SaveVideoScenes", {"versionId": "cv-nope", "scenes": SCENES}, prj_id).model_dump(),
        deps,
    )
    assert out["ok"] is False
    assert "NOT_FOUND" in out["error"]


async def test_update_backfills_tts_and_image() -> None:
    """配音/配图阶段回填：单幕可重渲就靠这条路径。"""
    conn, deps, prj_id, cv_id = _seed()
    saved = await dispatch(
        _env("SaveVideoScenes", {"versionId": cv_id, "scenes": SCENES}, prj_id).model_dump(),
        deps,
    )
    scene_id = saved["detail"]["sceneIds"][0]
    out = await dispatch(
        _env(
            "UpdateVideoScene",
            {
                "sceneId": scene_id,
                "audioUri": "file:///tmp/scene_00.m4a",
                "durationSec": 6.946,
                "bornAtSec": 0.35,
            },
            prj_id,
        ).model_dump(),
        deps,
    )
    assert out["ok"] is True, out
    scene = out["detail"]["scene"]
    assert scene["audio_uri"] == "file:///tmp/scene_00.m4a"
    assert scene["duration_sec"] == 6.946  # 实测时长，不是估值
    assert scene["born_at_sec"] == 0.35  # 抽帧目检前移取样用
    # 文案类字段没被动过
    assert scene["text"] == "今天我想讲一个观点。"


async def test_update_rejects_unknown_field() -> None:
    """文案字段不允许从回填路径改，否则「改一句重渲全片」会从后门溜回来。"""
    conn, deps, prj_id, cv_id = _seed()
    saved = await dispatch(
        _env("SaveVideoScenes", {"versionId": cv_id, "scenes": SCENES}, prj_id).model_dump(),
        deps,
    )
    scene_id = saved["detail"]["sceneIds"][0]
    out = await dispatch(
        _env(
            "UpdateVideoScene",
            {"sceneId": scene_id, "text": "偷偷改文案"},
            prj_id,
        ).model_dump(),
        deps,
    )
    assert out["ok"] is False
    assert "INVALID_ARGUMENT" in out["error"]


async def test_update_unknown_scene_not_found() -> None:
    conn, deps, prj_id, cv_id = _seed()
    out = await dispatch(
        _env("UpdateVideoScene", {"sceneId": "vs-nope", "durationSec": 1.0}, prj_id).model_dump(),
        deps,
    )
    assert out["ok"] is False
    assert "NOT_FOUND" in out["error"]


async def test_agent_can_list_but_not_write() -> None:
    """PRD §9.1：外部 Agent 只读放行，写命令降级为待审批的准备任务。"""
    conn, deps, prj_id, cv_id = _seed()
    listed = await dispatch(
        _env(
            "ListVideoScenes",
            {"versionId": cv_id},
            prj_id,
            actor={"type": "agent", "id": "a1"},
            source="mcp",
        ).model_dump(),
        deps,
    )
    assert listed["ok"] is True, listed

    wrote = await dispatch(
        _env(
            "SaveVideoScenes",
            {"versionId": cv_id, "scenes": SCENES},
            prj_id,
            actor={"type": "agent", "id": "a1"},
            source="mcp",
        ).model_dump(),
        deps,
    )
    assert wrote["ok"] is False
    assert "FORBIDDEN_ACTOR" in wrote["error"]


def test_unique_seq_index_exists() -> None:
    """UNIQUE(version_id, seq)：并发写幕不该互相覆盖成两条同序号记录。"""
    conn = in_memory()
    run_migrations(conn, migrations_dir())
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='idx_video_scenes_version_seq'"
    ).fetchall()
    assert rows, "缺少 UNIQUE(version_id, seq) 索引"


@pytest.mark.parametrize("command", ["SaveVideoScenes", "ListVideoScenes", "UpdateVideoScene"])
def test_commands_are_routed(command: str) -> None:
    """命令总线必须真的认识这三个命令（表建了但没入口 = 空表）。"""
    from worker.runtime.commands.bus import _ROUTES

    assert _ROUTES.get(command) == "worker.runtime.handlers.video_scenes"
