"""平台填充包测试（PRD-PUB-003 / ADR-008 FILL_AND_PREVIEW）。

ADR-008 决定 V0.1–V0.5 只支持「自动填写 + 停在预览页」，最终发布必须
用户手动点击。因此这里重点锁死：
1. 包里明确标记 auto_publish=False（消费方据此知道不得点发布）；
2. 平台约束在**提交前**校验（等填到页面上才发现超长就晚了）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentProject, ContentVersion, Workspace
from worker.runtime.publish.platforms import (
    FILL_MODE,
    PLATFORM_RULES,
    build_fill_package,
    resolve_rules,
    validate_fields,
)

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


# ----- 纯函数：平台规则与校验 -----


def test_resolve_rules_known_and_unknown() -> None:
    assert resolve_rules("douyin").id == "douyin"
    assert resolve_rules("douyin").requires_video is True
    # 未知平台回落 generic，不阻断
    assert resolve_rules("no-such-platform").id == "generic"


def test_validate_flags_oversized_title_and_body() -> None:
    rules = PLATFORM_RULES["douyin"]
    issues = validate_fields(
        rules,
        title="标" * 50,
        body="正" * 2000,
        tags=[],
        has_video=True,
    )
    fields = {i["field"] for i in issues}
    assert "title" in fields
    assert "body" in fields
    assert all(i["level"] == "error" for i in issues if i["field"] in ("title", "body"))


def test_validate_flags_too_many_tags_as_warning() -> None:
    rules = PLATFORM_RULES["douyin"]
    issues = validate_fields(
        rules, title="ok", body="ok", tags=[f"t{i}" for i in range(20)], has_video=True
    )
    tag_issues = [i for i in issues if i["field"] == "tags"]
    assert tag_issues
    # 标签超量只是 warning（平台会忽略多余的），不该拦住用户
    assert tag_issues[0]["level"] == "warning"


def test_validate_requires_video_for_douyin() -> None:
    issues = validate_fields(
        PLATFORM_RULES["douyin"], title="ok", body="ok", tags=[], has_video=False
    )
    assert any(i["field"] == "video" and i["level"] == "error" for i in issues)
    # generic 不要求视频
    assert not validate_fields(
        PLATFORM_RULES["generic"], title="ok", body="ok", tags=[], has_video=False
    )


def test_validate_rejects_empty_title() -> None:
    issues = validate_fields(
        PLATFORM_RULES["generic"], title="   ", body="b", tags=[], has_video=True
    )
    assert any(i["field"] == "title" and i["level"] == "error" for i in issues)


def test_package_never_authorizes_auto_publish() -> None:
    """ADR-008 核心约束：包里必须明确禁止自动发布。"""
    pkg = build_fill_package(
        variant={"platform": "douyin", "title": "标题", "body": "正文", "tags": ["a"]},
        video_path="/tmp/v.mp4",
        cover_path=None,
    )
    assert pkg["mode"] == FILL_MODE
    assert pkg["auto_publish"] is False
    assert pkg["requires_manual_publish"] is True


def test_package_ready_flag_reflects_errors_not_warnings() -> None:
    ok = build_fill_package(
        variant={"platform": "douyin", "title": "短标题", "body": "正文", "tags": ["a"]},
        video_path="/tmp/v.mp4",
        cover_path=None,
    )
    assert ok["ready"] is True

    # warning（标签超量）不该把 ready 打成 False
    warned = build_fill_package(
        variant={
            "platform": "douyin",
            "title": "短标题",
            "body": "正文",
            "tags": [f"t{i}" for i in range(20)],
        },
        video_path="/tmp/v.mp4",
        cover_path=None,
    )
    assert warned["ready"] is True
    assert warned["issues"]

    # error（缺视频）则 ready=False
    blocked = build_fill_package(
        variant={"platform": "douyin", "title": "短标题", "body": "正文", "tags": []},
        video_path=None,
        cover_path=None,
    )
    assert blocked["ready"] is False


# ----- 端到端 -----


def _setup() -> tuple[Deps, str, str]:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos = Repos(c)
    ws = repos.workspaces.insert(Workspace(name="ws-f", root_path="/tmp/f"))
    prj = repos.projects.insert(ContentProject(workspace_id=ws, title="p"))
    repos.content_versions.insert(
        ContentVersion(
            project_id=prj, content_type="script", content="主稿", content_hash="a"
        )
    )
    return Deps(repos=repos, ingest=ingest), ws, prj


def _env(ct: str, payload: dict[str, Any], ws: str, prj: str) -> dict[str, Any]:
    return {
        "commandId": "cmd-f",
        "commandType": ct,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u"},
        "source": "ui",
        "workspaceId": ws,
        "projectId": prj,
        "payload": payload,
        "requestedAt": "2026-07-27T00:00:00+00:00",
    }


async def test_build_fill_package_end_to_end() -> None:
    deps, ws, prj = _setup()
    created = await dispatch(
        _env(
            "CreatePlatformVariant",
            {
                "projectId": prj,
                "platform": "douyin",
                "title": "一个不超长的标题",
                "body": "正文内容",
                "tags": ["效率"],
            },
            ws,
            prj,
        ),
        deps,
    )
    assert created["ok"] is True, created.get("error")
    variant_id = created["detail"]["variant"]["id"]

    res = await dispatch(
        _env("BuildPlatformFillPackage", {"variantId": variant_id}, ws, prj), deps
    )
    assert res["ok"] is True, res.get("error")
    pkg = res["detail"]["fill_package"]
    assert pkg["platform"] == "douyin"
    assert pkg["fields"]["title"] == "一个不超长的标题"
    assert pkg["fields"]["tags"] == ["效率"]
    assert pkg["auto_publish"] is False
    # 无渲染产物 → 抖音要求视频，ready=False 且给出可读原因
    assert pkg["ready"] is False
    assert any("视频" in i["message"] for i in pkg["issues"])


async def test_build_fill_package_unknown_variant() -> None:
    deps, ws, prj = _setup()
    res = await dispatch(
        _env("BuildPlatformFillPackage", {"variantId": "nope"}, ws, prj), deps
    )
    assert res["ok"] is False
    assert "NOT_FOUND" in res["error"]
