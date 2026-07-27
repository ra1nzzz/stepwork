"""定时发布测试。

核心不是「能不能存一条排期」，而是**两种模式有没有被如实区分**：

- ``platform_native``：平台自己会在到点发布 → 真正无人值守；
- ``local_reminder``：平台没有原生定时 → 到点只能提醒用户。

把后者说成「定时发布」是危险的：用户会以为可以去睡觉。所以有专门用例
守住 ``unattended`` 标志与文案措辞。

平台窗口取自各平台公开规则（抖音 2h~7d、B站 ≤24h、小红书 ≤7d、
视频号无可用原生定时）。
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import connect
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentVersion
from worker.runtime.publish import schedule
from worker.runtime.publish.platforms import (
    SCHEDULE_LOCAL,
    SCHEDULE_NATIVE,
    build_fill_package,
    resolve_rules,
    resolve_schedule_mode,
)

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"
_NOW = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)


def _env(command_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "desktop", "id": "ui"},
        "source": "ui",
        "workspaceId": "ws-local",
        "requestedAt": datetime.now(UTC).isoformat(),
        "payload": payload or {},
    }


def _new_db(tmp_path: Path) -> tuple[sqlite3.Connection, Repos]:
    conn = connect(str(tmp_path / "sched.db"))
    run_migrations(conn, _MIG_DIR)
    return conn, Repos(conn)


def _run(raw: dict[str, Any], deps: Deps) -> dict[str, Any]:
    return asyncio.run(dispatch(raw, deps))


def _seed_variant(deps: Deps, platform: str) -> str:
    """建项目 + 主稿 + 该平台变体，返回 variant id。

    变体必须锚定一个 content_version（发布得有主稿），所以先落一版脚本。
    """
    pid = _run(_env("CreateProject", {"title": "定时"}), deps)["detail"]["project"]["id"]
    deps.repos.content_versions.insert(
        ContentVersion(
            project_id=pid,
            content_type="script",
            content='{"title": "主稿", "body": "正文"}',
            content_hash="h_master",
        )
    )
    res = _run(
        _env(
            "CreatePlatformVariant",
            {
                "projectId": pid,
                "platform": platform,
                "title": "标题",
                "body": "正文",
                "tags": ["a"],
            },
        ),
        deps,
    )
    assert res["ok"] is True, res
    return str(res["detail"]["variant"]["id"])


# --------------------------------------------------------------------------
# 模式判定：必须与各平台真实窗口一致
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "hours", "expected"),
    [
        # 抖音：需提前 ≥2h，最多 7 天
        ("douyin", 1, SCHEDULE_LOCAL),
        ("douyin", 3, SCHEDULE_NATIVE),
        ("douyin", 24 * 6, SCHEDULE_NATIVE),
        ("douyin", 24 * 8, SCHEDULE_LOCAL),
        # B站：窗口约 24 小时
        ("bilibili", 3, SCHEDULE_NATIVE),
        ("bilibili", 48, SCHEDULE_LOCAL),
        # 小红书：最多 7 天，无最小提前量
        ("xiaohongshu", 1, SCHEDULE_NATIVE),
        ("xiaohongshu", 24 * 8, SCHEDULE_LOCAL),
        # 视频号：官方定时不稳定，一律按无原生能力处理
        ("weixin_channels", 3, SCHEDULE_LOCAL),
        ("weixin_channels", 24 * 3, SCHEDULE_LOCAL),
    ],
)
def test_schedule_mode_matches_platform_window(
    platform: str, hours: int, expected: str
) -> None:
    rules = resolve_rules(platform)
    mode = resolve_schedule_mode(rules, _NOW + timedelta(hours=hours), _NOW)
    assert mode == expected


def test_out_of_window_degrades_with_explanation() -> None:
    """超窗要降级并说清原因，而不是静默按原生排（那会排不上）。"""
    rules = resolve_rules("douyin")
    block = schedule_block(rules, hours=1)
    assert block["mode"] == SCHEDULE_LOCAL
    assert block["fill_native_field"] is False
    assert any("至少提前" in i["message"] for i in block["issues"]), block["issues"]


def schedule_block(rules: Any, *, hours: int) -> dict[str, Any]:
    from worker.runtime.publish.platforms import build_schedule_block

    return build_schedule_block(rules, _NOW + timedelta(hours=hours), _NOW)


def test_native_block_tells_plugin_to_fill_platform_field() -> None:
    block = schedule_block(resolve_rules("douyin"), hours=5)
    assert block["mode"] == SCHEDULE_NATIVE
    assert block["fill_native_field"] is True
    # 即便走原生定时，提交动作仍必须由人点（ADR-008 不因定时而放宽）
    assert block["requires_manual_submit"] is True
    assert "手机端" in block["note"]


def test_past_time_is_an_error_not_a_degrade() -> None:
    block = schedule_block(resolve_rules("douyin"), hours=-1)
    assert any(i["level"] == "error" for i in block["issues"])


# --------------------------------------------------------------------------
# 填充包
# --------------------------------------------------------------------------


def test_fill_package_without_schedule_is_unchanged() -> None:
    """不传时间 = 立即发布，行为与加定时功能之前完全一致。"""
    pkg = build_fill_package(
        variant={"platform": "douyin", "title": "t", "body": "b", "tags": []},
        video_path="/tmp/v.mp4",
        cover_path=None,
    )
    assert pkg["schedule"] is None
    assert pkg["auto_publish"] is False


def test_fill_package_carries_schedule_block() -> None:
    pkg = build_fill_package(
        variant={"platform": "douyin", "title": "t", "body": "b", "tags": []},
        video_path="/tmp/v.mp4",
        cover_path=None,
        scheduled_at=_NOW + timedelta(hours=5),
        now=_NOW,
    )
    assert pkg["schedule"]["mode"] == SCHEDULE_NATIVE
    # 定时不改变「绝不自动点发布」这条（ADR-008）
    assert pkg["auto_publish"] is False
    assert pkg["requires_manual_publish"] is True


def test_past_schedule_makes_package_not_ready() -> None:
    """排到过去的时间属于 error，包不应被判为可提交。"""
    pkg = build_fill_package(
        variant={"platform": "douyin", "title": "t", "body": "b", "tags": []},
        video_path="/tmp/v.mp4",
        cover_path=None,
        scheduled_at=_NOW - timedelta(hours=1),
        now=_NOW,
    )
    assert pkg["ready"] is False


# --------------------------------------------------------------------------
# 队列命令
# --------------------------------------------------------------------------


def test_schedule_publish_reports_unattended_honestly(tmp_path: Path) -> None:
    """两种模式的 unattended 标志与文案必须不同 —— 这是本功能的安全核心。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        when = (datetime.now(UTC) + timedelta(hours=5)).isoformat()

        native = _run(
            _env(
                "SchedulePublish",
                {"variantId": _seed_variant(deps, "douyin"), "scheduledAt": when},
            ),
            deps,
        )
        assert native["ok"] is True, native
        assert native["detail"]["mode"] == SCHEDULE_NATIVE
        assert native["detail"]["unattended"] is True
        assert "平台自动发布" in native["detail"]["mode_description"]

        local = _run(
            _env(
                "SchedulePublish",
                {
                    "variantId": _seed_variant(deps, "weixin_channels"),
                    "scheduledAt": when,
                },
            ),
            deps,
        )
        assert local["detail"]["mode"] == SCHEDULE_LOCAL
        assert local["detail"]["unattended"] is False
        # 措辞必须说明「提醒你手动发布」，不能包装成自动发布
        assert "提醒你手动发布" in local["detail"]["mode_description"]
    finally:
        conn.close()


def test_schedule_rejects_past_time(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        res = _run(
            _env(
                "SchedulePublish",
                {
                    "variantId": _seed_variant(deps, "douyin"),
                    "scheduledAt": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                },
            ),
            deps,
        )
        assert res["ok"] is False
        assert str(res["error"]).startswith("INVALID_ARGUMENT")
    finally:
        conn.close()


def test_naive_datetime_is_treated_as_utc() -> None:
    """无时区的时间不能直接参与比较（会 TypeError），也不能歧义差 8 小时。"""
    parsed = schedule.parse_scheduled_at("2026-08-01T12:00:00")
    assert parsed.tzinfo is not None
    assert schedule.parse_scheduled_at("2026-08-01T12:00:00Z") == parsed


def test_cancel_then_cancel_again_is_rejected(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        sid = _run(
            _env(
                "SchedulePublish",
                {
                    "variantId": _seed_variant(deps, "douyin"),
                    "scheduledAt": (datetime.now(UTC) + timedelta(hours=5)).isoformat(),
                },
            ),
            deps,
        )["detail"]["id"]

        assert _run(_env("CancelScheduledPublish", {"scheduleId": sid}), deps)["ok"]
        again = _run(_env("CancelScheduledPublish", {"scheduleId": sid}), deps)
        # 不能假装成功：已取消的就该如实报错
        assert again["ok"] is False
        assert str(again["error"]).startswith("INVALID_STATE")
    finally:
        conn.close()


def test_fire_marks_due_and_skips_future(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        past_vid = _seed_variant(deps, "douyin")
        future_vid = _seed_variant(deps, "douyin")
        now = datetime.now(UTC)
        # 直接用底层 create 绕过「必须是未来」的校验，构造一条已到期的
        schedule.create(
            conn,
            workspace_id="ws-local",
            project_id=_run(_env("ListProjects", {}), deps)["detail"]["projects"][0]["id"],
            variant_id=past_vid,
            platform="douyin",
            scheduled_at=now + timedelta(hours=5),
            content_hash=schedule.current_content_hash(conn, past_vid),
            now=now,
        )
        conn.execute(
            "UPDATE scheduled_publishes SET scheduled_at=?",
            ((now - timedelta(minutes=1)).isoformat(),),
        )
        conn.commit()
        _run(
            _env(
                "SchedulePublish",
                {
                    "variantId": future_vid,
                    "scheduledAt": (now + timedelta(hours=5)).isoformat(),
                },
            ),
            deps,
        )

        fired = _run(_env("FireDueSchedules"), deps)["detail"]
        assert fired["count"] == 1, fired
        assert fired["fired"][0]["variant_id"] == past_vid
        # 再次触发不该重复（已 armed）
        assert _run(_env("FireDueSchedules"), deps)["detail"]["count"] == 0
    finally:
        conn.close()


def test_fire_flags_content_changed_after_scheduling(tmp_path: Path) -> None:
    """排期之后又改了稿：到点必须标出来，不能悄悄按旧排期走。

    与 PRD-PUB-004「授权与内容绑定」同一条原则 —— 排的是那份内容。
    """
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        vid = _seed_variant(deps, "douyin")
        now = datetime.now(UTC)
        pid = _run(_env("ListProjects", {}), deps)["detail"]["projects"][0]["id"]
        schedule.create(
            conn,
            workspace_id="ws-local",
            project_id=pid,
            variant_id=vid,
            platform="douyin",
            scheduled_at=now + timedelta(hours=5),
            content_hash=schedule.current_content_hash(conn, vid),
            now=now,
        )
        # 用户改了标题
        conn.execute(
            "UPDATE platform_variants SET title=? WHERE id=?", ("改过的标题", vid)
        )
        conn.execute(
            "UPDATE scheduled_publishes SET scheduled_at=?",
            ((now - timedelta(minutes=1)).isoformat(),),
        )
        conn.commit()

        fired = _run(_env("FireDueSchedules"), deps)["detail"]["fired"]
        assert len(fired) == 1
        assert fired[0]["content_changed"] is True
    finally:
        conn.close()


def test_fire_does_not_flag_unchanged_content(tmp_path: Path) -> None:
    """没改稿的不能误报，否则提示会变成噪声、用户就不看了。"""
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        vid = _seed_variant(deps, "douyin")
        now = datetime.now(UTC)
        pid = _run(_env("ListProjects", {}), deps)["detail"]["projects"][0]["id"]
        schedule.create(
            conn,
            workspace_id="ws-local",
            project_id=pid,
            variant_id=vid,
            platform="douyin",
            scheduled_at=now + timedelta(hours=5),
            content_hash=schedule.current_content_hash(conn, vid),
            now=now,
        )
        conn.execute(
            "UPDATE scheduled_publishes SET scheduled_at=?",
            ((now - timedelta(minutes=1)).isoformat(),),
        )
        conn.commit()
        fired = _run(_env("FireDueSchedules"), deps)["detail"]["fired"]
        assert fired[0]["content_changed"] is False
    finally:
        conn.close()


def test_list_scheduled_filters_by_status(tmp_path: Path) -> None:
    conn, repos = _new_db(tmp_path)
    try:
        deps = Deps(repos=repos)
        when = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
        keep = _run(
            _env(
                "SchedulePublish",
                {"variantId": _seed_variant(deps, "douyin"), "scheduledAt": when},
            ),
            deps,
        )["detail"]["id"]
        drop = _run(
            _env(
                "SchedulePublish",
                {"variantId": _seed_variant(deps, "douyin"), "scheduledAt": when},
            ),
            deps,
        )["detail"]["id"]
        _run(_env("CancelScheduledPublish", {"scheduleId": drop}), deps)

        pending = _run(_env("ListScheduledPublishes", {"status": "pending"}), deps)
        ids = [s["id"] for s in pending["detail"]["scheduled"]]
        assert ids == [keep]
        every = _run(_env("ListScheduledPublishes", {}), deps)["detail"]["scheduled"]
        assert len(every) == 2
    finally:
        conn.close()


def test_external_agents_cannot_schedule_publishes(tmp_path: Path) -> None:
    """定时发布是发布链路的一环，外部 Agent 不得自行排期。"""
    conn, repos = _new_db(tmp_path)
    try:
        raw = _env("SchedulePublish", {"variantId": "v", "scheduledAt": "2026-08-01T00:00:00Z"})
        raw["actor"] = {"type": "agent", "id": "evil"}
        raw["source"] = "mcp"
        res = _run(raw, Deps(repos=repos))
        assert res["ok"] is False
        assert str(res["error"]).startswith("FORBIDDEN_ACTOR"), res
    finally:
        conn.close()
