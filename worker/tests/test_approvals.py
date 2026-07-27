"""审批中心测试（PRD-AGT-008 / §9.1 降级 / §9.2 展示要素 / PRD-PUB-004）。

``approval_requests`` 表 0003 就建好了，但此前全仓零写入零读取零 UI，
§9 整章形同虚设。这里覆盖：降级为准备任务、七要素展示、决策状态机、
一次性授权与内容绑定。
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
from worker.runtime.handlers.approvals import content_hash

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _deps() -> Deps:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos = Repos(c)
    repos.workspaces.ensure("ws-ap")
    return Deps(repos=repos, ingest=ingest)


def _env(
    command_type: str,
    payload: dict[str, Any] | None = None,
    *,
    actor_type: str = "user",
    source: str = "ui",
) -> dict[str, Any]:
    return {
        "commandId": "cmd-ap",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": actor_type, "id": "someone"},
        "source": source,
        "workspaceId": "ws-ap",
        "projectId": None,
        "payload": payload or {},
        "requestedAt": "2026-07-27T00:00:00+00:00",
    }


# ----- §9.1：被拒的 agent 请求降级为准备任务 -----


async def test_denied_agent_command_creates_preparation_task() -> None:
    """§9.1 原文是「只能创建准备任务」——只拒绝等于无路可走，不达标。"""
    deps = _deps()
    res = await dispatch(
        _env("DeleteAsset", {"assetId": "a1"}, actor_type="agent", source="mcp"),
        deps,
    )
    assert res["ok"] is False
    assert "FORBIDDEN_ACTOR" in res["error"]
    # 降级：给出可审批的准备任务 id
    approval_id = (res.get("detail") or {}).get("approval_id")
    assert approval_id, "被拒的高风险请求必须降级为准备任务"

    row = deps.repos.conn.execute(
        "SELECT * FROM approval_requests WHERE id=?", (approval_id,)
    ).fetchone()
    assert row is not None
    assert row["action_type"] == "DeleteAsset"
    assert row["status"] == "pending"
    assert row["actor"].startswith("agent:")


async def test_allowed_agent_command_creates_no_approval() -> None:
    """允许清单内的命令不该产生审批噪声。"""
    deps = _deps()
    res = await dispatch(
        _env("ListProjects", actor_type="agent", source="mcp"), deps
    )
    assert res["ok"] is True
    n = deps.repos.conn.execute(
        "SELECT COUNT(*) n FROM approval_requests"
    ).fetchone()["n"]
    assert n == 0


async def test_agent_can_request_but_cannot_decide() -> None:
    """Agent 可以申请审批（降级路径），但绝不能自批自用。"""
    deps = _deps()
    created = await dispatch(
        _env(
            "CreateApprovalRequest",
            {"actionType": "ExportBundle", "target": "prj-1"},
            actor_type="agent",
            source="mcp",
        ),
        deps,
    )
    assert created["ok"] is True, created.get("error")
    approval_id = created["detail"]["approval"]["id"]

    # 自己批准自己 → 必须被拒
    decided = await dispatch(
        _env(
            "DecideApprovalRequest",
            {"approvalId": approval_id, "decision": "approve"},
            actor_type="agent",
            source="mcp",
        ),
        deps,
    )
    assert decided["ok"] is False
    assert "FORBIDDEN_ACTOR" in decided["error"]


# ----- §9.2：审批展示要素 -----


async def test_list_returns_all_section_9_2_elements() -> None:
    """§9.2 要求展示：请求者/协议/目标/数据/费用风险/有效期/授权范围。"""
    deps = _deps()
    await dispatch(
        _env(
            "CreateApprovalRequest",
            {
                "actionType": "ExportBundle",
                "target": "prj-9",
                "scope": "once",
                "riskSummary": "将导出视频与封面到磁盘",
                "payload": {"variantId": "pv-1"},
            },
        ),
        deps,
    )
    listed = await dispatch(_env("ListApprovalRequests", {}), deps)
    assert listed["ok"] is True
    item = listed["detail"]["approvals"][0]

    assert item["actor"]  # 请求者
    assert item["action_type"] == "ExportBundle"  # 动作
    assert item["target"] == "prj-9"  # 目标
    assert item["payload"]["variantId"] == "pv-1"  # 将涉及的数据
    assert item["risk_summary"]  # 费用/风险
    assert item["expires_at"]  # 有效期
    assert item["requested_scope"] == "once"  # 一次性/持久
    assert listed["detail"]["pending_count"] == 1


async def test_list_filters_by_status() -> None:
    deps = _deps()
    await dispatch(
        _env("CreateApprovalRequest", {"actionType": "A", "target": "t"}), deps
    )
    res = await dispatch(
        _env("ListApprovalRequests", {"status": "approved"}), deps
    )
    assert res["ok"] is True
    assert res["detail"]["approvals"] == []


# ----- 决策状态机 -----


async def test_approve_and_reject_record_decision() -> None:
    deps = _deps()
    created = await dispatch(
        _env("CreateApprovalRequest", {"actionType": "X", "target": "t"}), deps
    )
    approval_id = created["detail"]["approval"]["id"]

    decided = await dispatch(
        _env(
            "DecideApprovalRequest",
            {"approvalId": approval_id, "decision": "approve"},
        ),
        deps,
    )
    assert decided["ok"] is True, decided.get("error")
    approval = decided["detail"]["approval"]
    assert approval["status"] == "approved"
    assert approval["decision_actor"].startswith("user:")
    assert approval["decision_at"]
    # 批准 != 自动执行（默认不过度自动化）
    assert decided["detail"]["auto_executed"] is False


async def test_cannot_decide_twice() -> None:
    """已决不可二次裁决（避免批准一个早已处理过的高风险操作）。"""
    deps = _deps()
    created = await dispatch(
        _env("CreateApprovalRequest", {"actionType": "X", "target": "t"}), deps
    )
    approval_id = created["detail"]["approval"]["id"]
    await dispatch(
        _env(
            "DecideApprovalRequest",
            {"approvalId": approval_id, "decision": "reject"},
        ),
        deps,
    )
    again = await dispatch(
        _env(
            "DecideApprovalRequest",
            {"approvalId": approval_id, "decision": "approve"},
        ),
        deps,
    )
    assert again["ok"] is False
    assert "already rejected" in again["error"]


async def test_expired_request_is_marked_and_not_decidable() -> None:
    """§9.2 的「有效期」必须真正生效，而不是只存个字段。"""
    deps = _deps()
    created = await dispatch(
        _env("CreateApprovalRequest", {"actionType": "X", "target": "t"}), deps
    )
    approval_id = created["detail"]["approval"]["id"]
    # 手工把有效期拨到过去
    deps.repos.conn.execute(
        "UPDATE approval_requests SET expires_at='2020-01-01T00:00:00+00:00' "
        "WHERE id=?",
        (approval_id,),
    )
    deps.repos.conn.commit()

    listed = await dispatch(_env("ListApprovalRequests", {}), deps)
    assert listed["detail"]["approvals"][0]["status"] == "expired"

    decided = await dispatch(
        _env(
            "DecideApprovalRequest",
            {"approvalId": approval_id, "decision": "approve"},
        ),
        deps,
    )
    assert decided["ok"] is False
    assert "expired" in decided["error"]


async def test_invalid_decision_and_scope_rejected() -> None:
    deps = _deps()
    bad_scope = await dispatch(
        _env(
            "CreateApprovalRequest",
            {"actionType": "X", "target": "t", "scope": "forever"},
        ),
        deps,
    )
    assert bad_scope["ok"] is False
    assert "INVALID_ARGUMENT" in bad_scope["error"]

    created = await dispatch(
        _env("CreateApprovalRequest", {"actionType": "X", "target": "t"}), deps
    )
    bad_decision = await dispatch(
        _env(
            "DecideApprovalRequest",
            {
                "approvalId": created["detail"]["approval"]["id"],
                "decision": "maybe",
            },
        ),
        deps,
    )
    assert bad_decision["ok"] is False
    assert "INVALID_ARGUMENT" in bad_decision["error"]


# ----- PRD-PUB-004：授权与内容哈希绑定 -----


def test_content_hash_binds_authorization_to_content() -> None:
    """PRD-PUB-004：授权与内容绑定 —— 内容变了哈希就变，旧授权自然失效。"""
    a = content_hash({"title": "标题", "body": "正文"})
    b = content_hash({"body": "正文", "title": "标题"})  # 键序无关
    c = content_hash({"title": "标题", "body": "改过的正文"})
    assert a == b
    assert a != c


async def test_approval_payload_carries_content_hash() -> None:
    deps = _deps()
    created = await dispatch(
        _env(
            "CreateApprovalRequest",
            {"actionType": "ExportBundle", "target": "t", "payload": {"v": 1}},
        ),
        deps,
    )
    payload = created["detail"]["approval"]["payload"]
    assert payload["content_hash"] == content_hash({"v": 1})


# ----- 降级为准备任务必须有去重与限流（否则是本地 DoS） -----


async def test_repeated_denials_reuse_one_pending_request() -> None:
    """同一 actor 反复撞同一禁令，只应有一条待审批（而非灌满库）。"""
    deps = _deps()
    ids = set()
    for _ in range(10):
        res = await dispatch(
            _env("DeleteAsset", {"assetId": "a1"}, actor_type="agent", source="mcp"),
            deps,
        )
        assert res["ok"] is False
        ids.add((res.get("detail") or {}).get("approval_id"))

    n = deps.repos.conn.execute(
        "SELECT COUNT(*) n FROM approval_requests"
    ).fetchone()["n"]
    assert n == 1, f"10 次被拒产生了 {n} 条审批请求"
    assert len(ids) == 1, "重复请求应复用同一条准备任务"


async def test_pending_approvals_are_capped_per_actor() -> None:
    """用不同 target 绕过去重时，仍受单 actor 待审批上限约束。"""
    from worker.runtime.commands.bus import _MAX_PENDING_APPROVALS_PER_ACTOR

    deps = _deps()
    over = _MAX_PENDING_APPROVALS_PER_ACTOR + 10
    for i in range(over):
        env = _env(
            "DeleteAsset", {"assetId": f"a{i}"}, actor_type="agent", source="mcp"
        )
        env["projectId"] = f"prj-{i}"  # 每次换目标，绕开去重
        await dispatch(env, deps)

    n = deps.repos.conn.execute(
        "SELECT COUNT(*) n FROM approval_requests WHERE status='pending'"
    ).fetchone()["n"]
    assert n <= _MAX_PENDING_APPROVALS_PER_ACTOR, f"待审批 {n} 条，超过上限"


async def test_denial_still_rejects_when_cap_reached() -> None:
    """达到上限后仍必须拒绝命令本身——限流只影响登记，不影响安全语义。"""
    from worker.runtime.commands.bus import _MAX_PENDING_APPROVALS_PER_ACTOR

    deps = _deps()
    for i in range(_MAX_PENDING_APPROVALS_PER_ACTOR + 5):
        env = _env(
            "DeleteAsset", {"assetId": f"b{i}"}, actor_type="agent", source="mcp"
        )
        env["projectId"] = f"p-{i}"
        res = await dispatch(env, deps)
        assert res["ok"] is False
        assert "FORBIDDEN_ACTOR" in res["error"]
