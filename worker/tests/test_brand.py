"""Tranche 2：BrandProfile CRUD + 生成注入 + producer 记录测试。

覆盖：

1. CreateBrandProfile 全字段 camelCase 出参。
2. UpdateBrandProfile 局部更新 + updated_at 刷新 + NOT_FOUND。
3. ListBrandProfiles created_at DESC + 工作区隔离。
4. SetProjectBrandProfile 关联/解绑（null）+ NOT_FOUND。
5. GenerateTopic / GenerateScript：prompt 注入（语气 + 「不得使用以下表达」）
   与 producer 记录 brand_profile_id / brand_profile_updated_at。
6. 无 profile 时 producer 字段为 null（向后兼容）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentProject, ContentVersion, Workspace

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


class _RecordingAIProvider:
    """记录 prompt 的 Fake AI（供注入断言）。"""

    name = "fake-ai"
    model = "fake-1"
    estimated_cost_per_1k = 0.0

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.prompts.append(prompt)
        if schema and "angles" in schema.get("properties", {}):
            return {
                "angles": [
                    {"id": "a1", "title": "角度一", "rationale": "r", "hook": "h"}
                ]
            }
        return {"title": "标题", "body": "正文"}


def _deps(ai: Any = None) -> Deps:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    return Deps(repos=Repos(conn), ingest=ingest, ai=ai)


def _env(
    command_type: str,
    payload: dict[str, Any] | None = None,
    workspace_id: str = "ws-brand",
    project_id: str | None = None,
) -> dict[str, Any]:
    return {
        "commandId": f"cid-{command_type}",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": workspace_id,
        "projectId": project_id,
        "payload": payload or {},
        "requestedAt": datetime.now(UTC).isoformat(),
    }


async def test_create_brand_profile_full_fields() -> None:
    deps = _deps()
    res = await dispatch(
        _env(
            "CreateBrandProfile",
            {
                "name": "科技实测",
                "positioning": "第一人称验证",
                "audience": "效率工具用户",
                "tone": "克制判断",
                "contentPillars": ["AI", "效率"],
                "bannedExpressions": ["天花板", "yyds"],
            },
        ),
        deps,
    )
    assert res["ok"] is True, res.get("error")
    profile = res["detail"]["profile"]
    assert profile["id"].startswith("bp_")
    assert profile["name"] == "科技实测"
    assert profile["contentPillars"] == ["AI", "效率"]
    assert profile["bannedExpressions"] == ["天花板", "yyds"]
    assert profile["createdAt"] and profile["updatedAt"]


async def test_create_brand_profile_missing_name() -> None:
    deps = _deps()
    res = await dispatch(_env("CreateBrandProfile", {}), deps)
    assert res["ok"] is False
    assert "INVALID_ARGUMENT" in res["error"]


async def test_update_brand_profile_partial() -> None:
    deps = _deps()
    created = await dispatch(
        _env("CreateBrandProfile", {"name": "旧名", "tone": "旧语气"}), deps
    )
    pid = created["detail"]["profile"]["id"]
    res = await dispatch(
        _env(
            "UpdateBrandProfile",
            {"profileId": pid, "tone": "新语气", "bannedExpressions": ["绝对"]},
        ),
        deps,
    )
    assert res["ok"] is True
    profile = res["detail"]["profile"]
    assert profile["name"] == "旧名"  # 未提供字段保持不变
    assert profile["tone"] == "新语气"
    assert profile["bannedExpressions"] == ["绝对"]
    assert profile["updatedAt"] >= profile["createdAt"]


async def test_update_brand_profile_not_found() -> None:
    deps = _deps()
    res = await dispatch(
        _env("UpdateBrandProfile", {"profileId": "bp_nope", "tone": "x"}), deps
    )
    assert res["ok"] is False
    assert "NOT_FOUND" in res["error"]


async def test_list_brand_profiles_scoped_desc() -> None:
    deps = _deps()
    await dispatch(_env("CreateBrandProfile", {"name": "P1"}), deps)
    await dispatch(_env("CreateBrandProfile", {"name": "P2"}), deps)
    # 其它工作区的 profile 不应出现
    await dispatch(
        _env("CreateBrandProfile", {"name": "别家"}, workspace_id="ws-other"), deps
    )
    res = await dispatch(_env("ListBrandProfiles"), deps)
    assert res["ok"] is True
    names = [p["name"] for p in res["detail"]["profiles"]]
    assert set(names) == {"P1", "P2"}
    created = [p["createdAt"] for p in res["detail"]["profiles"]]
    assert created == sorted(created, reverse=True)


async def test_set_project_brand_profile_and_unset() -> None:
    deps = _deps()
    prj = await dispatch(_env("CreateProject", {"title": "p"}), deps)
    project_id = prj["detail"]["project"]["id"]
    created = await dispatch(_env("CreateBrandProfile", {"name": "B"}), deps)
    profile_id = created["detail"]["profile"]["id"]

    res = await dispatch(
        _env(
            "SetProjectBrandProfile",
            {"projectId": project_id, "profileId": profile_id},
        ),
        deps,
    )
    assert res["ok"] is True
    row = deps.repos.conn.execute(
        "SELECT brand_profile_id FROM content_projects WHERE id=?", (project_id,)
    ).fetchone()
    assert row["brand_profile_id"] == profile_id

    # 解绑（profileId=null）
    res = await dispatch(
        _env(
            "SetProjectBrandProfile",
            {"projectId": project_id, "profileId": None},
        ),
        deps,
    )
    assert res["ok"] is True
    row = deps.repos.conn.execute(
        "SELECT brand_profile_id FROM content_projects WHERE id=?", (project_id,)
    ).fetchone()
    assert row["brand_profile_id"] is None


async def test_set_project_brand_profile_not_found() -> None:
    deps = _deps()
    res = await dispatch(
        _env("SetProjectBrandProfile", {"projectId": "prj_nope", "profileId": None}),
        deps,
    )
    assert res["ok"] is False
    assert "NOT_FOUND" in res["error"]


async def _setup_linked_project(deps: Deps) -> tuple[str, str, str, str]:
    """建 workspace/project/transcript 版本 + 关联 profile；返回 4 个 id。"""
    repos = deps.repos
    ws_id = repos.workspaces.insert(Workspace(name="ws", root_path="/tmp/ws"))
    prj_id = repos.projects.insert(ContentProject(workspace_id=ws_id, title="p"))
    cv_id = repos.content_versions.insert(
        ContentVersion(
            project_id=prj_id,
            content_type="transcript",
            content="素材文本",
            content_hash="h1",
        )
    )
    created = await dispatch(
        _env(
            "CreateBrandProfile",
            {
                "name": "品牌X",
                "tone": "克制第一人称",
                "positioning": "实测定位",
                "audience": "创作者受众",
                "contentPillars": ["支柱A"],
                "bannedExpressions": ["最强", "秒杀"],
            },
            workspace_id=ws_id,
        ),
        deps,
    )
    profile_id = created["detail"]["profile"]["id"]
    await dispatch(
        _env(
            "SetProjectBrandProfile",
            {"projectId": prj_id, "profileId": profile_id},
            workspace_id=ws_id,
        ),
        deps,
    )
    return ws_id, prj_id, cv_id, profile_id


async def test_generate_topic_injects_brand_and_records_producer() -> None:
    ai = _RecordingAIProvider()
    deps = _deps(ai)
    ws_id, prj_id, cv_id, profile_id = await _setup_linked_project(deps)

    res = await dispatch(
        _env(
            "GenerateTopic",
            {"source_version_id": cv_id, "count": 3},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert res["ok"] is True, res.get("error")
    prompt = ai.prompts[-1]
    assert "克制第一人称" in prompt
    assert "实测定位" in prompt
    assert "创作者受众" in prompt
    assert "支柱A" in prompt
    assert "不得使用以下表达" in prompt
    assert "最强" in prompt

    row = deps.repos.conn.execute(
        "SELECT producer FROM content_versions WHERE id=?",
        (res["artifact_ids"][0],),
    ).fetchone()
    import json

    producer = json.loads(row["producer"])
    assert producer["brand_profile_id"] == profile_id
    assert producer["brand_profile_updated_at"]


async def test_generate_script_injects_brand_and_records_producer() -> None:
    ai = _RecordingAIProvider()
    deps = _deps(ai)
    ws_id, prj_id, _cv_id, profile_id = await _setup_linked_project(deps)

    res = await dispatch(
        _env(
            "GenerateScript",
            {"outline": "大纲", "style": "short_video"},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert res["ok"] is True, res.get("error")
    assert res["detail"]["invocation"]["provider"] == "fake-ai"  # 费用透明
    prompt = ai.prompts[-1]
    assert "不得使用以下表达" in prompt
    assert "秒杀" in prompt

    row = deps.repos.conn.execute(
        "SELECT producer FROM content_versions WHERE id=?",
        (res["artifact_ids"][0],),
    ).fetchone()
    import json

    producer = json.loads(row["producer"])
    assert producer["brand_profile_id"] == profile_id


async def test_generate_topic_without_profile_backward_compatible() -> None:
    """无关联 profile：prompt 不含注入块，producer 字段为 null。"""
    ai = _RecordingAIProvider()
    deps = _deps(ai)
    repos = deps.repos
    ws_id = repos.workspaces.insert(Workspace(name="ws2", root_path="/tmp/ws2"))
    prj_id = repos.projects.insert(ContentProject(workspace_id=ws_id, title="p"))
    cv_id = repos.content_versions.insert(
        ContentVersion(
            project_id=prj_id,
            content_type="transcript",
            content="素材",
            content_hash="h2",
        )
    )
    res = await dispatch(
        _env(
            "GenerateTopic",
            {"source_version_id": cv_id, "count": 3},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert res["ok"] is True
    assert "不得使用以下表达" not in ai.prompts[-1]
    row = deps.repos.conn.execute(
        "SELECT producer FROM content_versions WHERE id=?",
        (res["artifact_ids"][0],),
    ).fetchone()
    import json

    producer = json.loads(row["producer"])
    assert producer["brand_profile_id"] is None
    assert producer["brand_profile_updated_at"] is None


# ----- PRD-BRD-002「生成时可选择启用」 -----


async def test_generate_topic_can_opt_out_of_brand_profile() -> None:
    """关闭开关时，即便项目已绑定品牌档也不注入、不记录。"""
    ai = _RecordingAIProvider()
    deps = _deps(ai)
    ws_id, prj_id, cv_id, _profile_id = await _setup_linked_project(deps)

    res = await dispatch(
        _env(
            "GenerateTopic",
            {"source_version_id": cv_id, "count": 3, "use_brand_profile": False},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert res["ok"] is True, res.get("error")

    prompt = ai.prompts[-1]
    # 品牌画像的特征串一个都不该出现
    assert "克制第一人称" not in prompt
    assert "实测定位" not in prompt
    assert "不得使用以下表达" not in prompt

    import json

    row = deps.repos.conn.execute(
        "SELECT producer FROM content_versions WHERE id=?",
        (res["artifact_ids"][0],),
    ).fetchone()
    producer = json.loads(row["producer"])
    # 可审计地表明「本次未使用品牌档」
    assert producer.get("brand_profile_id") is None


async def test_generate_script_can_opt_out_of_brand_profile() -> None:
    ai = _RecordingAIProvider()
    deps = _deps(ai)
    ws_id, prj_id, cv_id, _profile_id = await _setup_linked_project(deps)

    topic = await dispatch(
        _env(
            "GenerateTopic",
            {"source_version_id": cv_id, "count": 3},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert topic["ok"] is True

    res = await dispatch(
        _env(
            "GenerateScript",
            {
                "proposal_version_id": topic["artifact_ids"][0],
                "use_brand_profile": False,
            },
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert res["ok"] is True, res.get("error")
    assert "克制第一人称" not in ai.prompts[-1]


# ----- PRD-BRD-003 历史脚本（风格参考）/ PRD-BRD-004 偏好记录 -----


async def test_import_and_list_brand_scripts() -> None:
    """PRD-BRD-003：导入历史脚本并可检索。"""
    deps = _deps(_RecordingAIProvider())
    ws_id, _prj_id, _cv_id, profile_id = await _setup_linked_project(deps)

    imported = await dispatch(
        _env(
            "ImportBrandScript",
            {
                "profileId": profile_id,
                "title": "旧脚本一",
                "content": "这是我以前写的口播脚本，风格克制。",
                "source": "manual",
            },
            workspace_id=ws_id,
        ),
        deps,
    )
    assert imported["ok"] is True, imported.get("error")
    assert imported["artifact_ids"]

    listed = await dispatch(
        _env("ListBrandScripts", {"profileId": profile_id}, workspace_id=ws_id),
        deps,
    )
    assert listed["ok"] is True
    assert listed["detail"]["count"] == 1
    assert listed["detail"]["scripts"][0]["title"] == "旧脚本一"

    # 检索：命中与不命中
    hit = await dispatch(
        _env(
            "ListBrandScripts",
            {"profileId": profile_id, "keyword": "克制"},
            workspace_id=ws_id,
        ),
        deps,
    )
    assert hit["detail"]["count"] == 1
    miss = await dispatch(
        _env(
            "ListBrandScripts",
            {"profileId": profile_id, "keyword": "红烧肉"},
            workspace_id=ws_id,
        ),
        deps,
    )
    assert miss["detail"]["count"] == 0


async def test_reference_scripts_injected_into_prompt() -> None:
    """PRD-BRD-003「用于风格参考」：范文必须真的进 prompt。"""
    ai = _RecordingAIProvider()
    deps = _deps(ai)
    ws_id, prj_id, cv_id, profile_id = await _setup_linked_project(deps)
    await dispatch(
        _env(
            "ImportBrandScript",
            {
                "profileId": profile_id,
                "title": "范文标题",
                "content": "范文正文特征串-ABC",
            },
            workspace_id=ws_id,
        ),
        deps,
    )

    res = await dispatch(
        _env(
            "GenerateTopic",
            {"source_version_id": cv_id, "count": 3},
            workspace_id=ws_id,
            project_id=prj_id,
        ),
        deps,
    )
    assert res["ok"] is True, res.get("error")
    prompt = ai.prompts[-1]
    assert "范文正文特征串-ABC" in prompt, "历史脚本范文应注入 prompt"
    assert "模仿其文风" in prompt
    assert "不要照抄内容" in prompt


async def test_delete_brand_script() -> None:
    deps = _deps(_RecordingAIProvider())
    ws_id, _prj, _cv, profile_id = await _setup_linked_project(deps)
    imported = await dispatch(
        _env(
            "ImportBrandScript",
            {"profileId": profile_id, "content": "待删除"},
            workspace_id=ws_id,
        ),
        deps,
    )
    script_id = imported["artifact_ids"][0]

    deleted = await dispatch(
        _env("DeleteBrandScript", {"scriptId": script_id}, workspace_id=ws_id), deps
    )
    assert deleted["ok"] is True

    listed = await dispatch(
        _env("ListBrandScripts", {"profileId": profile_id}, workspace_id=ws_id), deps
    )
    assert listed["detail"]["count"] == 0

    again = await dispatch(
        _env("DeleteBrandScript", {"scriptId": script_id}, workspace_id=ws_id), deps
    )
    assert again["ok"] is False
    assert "NOT_FOUND" in again["error"]


async def test_import_brand_script_validates() -> None:
    deps = _deps(_RecordingAIProvider())
    ws_id, _prj, _cv, profile_id = await _setup_linked_project(deps)

    no_content = await dispatch(
        _env("ImportBrandScript", {"profileId": profile_id}, workspace_id=ws_id), deps
    )
    assert no_content["ok"] is False
    assert "INVALID_ARGUMENT" in no_content["error"]

    bad_profile = await dispatch(
        _env(
            "ImportBrandScript",
            {"profileId": "bp_nope", "content": "x"},
            workspace_id=ws_id,
        ),
        deps,
    )
    assert bad_profile["ok"] is False
    assert "NOT_FOUND" in bad_profile["error"]


async def test_record_preference_events() -> None:
    """PRD-BRD-004：记录采纳/修改/丢弃（此前完全没有记录）。"""
    deps = _deps(_RecordingAIProvider())
    ws_id, prj_id, _cv, profile_id = await _setup_linked_project(deps)

    for action in ("accepted", "modified", "discarded"):
        res = await dispatch(
            _env(
                "RecordPreference",
                {"profileId": profile_id, "action": action, "versionId": "cv-x"},
                workspace_id=ws_id,
                project_id=prj_id,
            ),
            deps,
        )
        assert res["ok"] is True, res.get("error")

    rows = deps.repos.conn.execute(
        "SELECT action FROM user_preference_events ORDER BY created_at"
    ).fetchall()
    assert {r["action"] for r in rows} == {"accepted", "modified", "discarded"}

    bad = await dispatch(
        _env(
            "RecordPreference",
            {"profileId": profile_id, "action": "loved"},
            workspace_id=ws_id,
        ),
        deps,
    )
    assert bad["ok"] is False
    assert "INVALID_ARGUMENT" in bad["error"]


async def test_one_profile_can_be_linked_to_multiple_projects() -> None:
    """PRD-BRD-001 的验收是「**可关联多个项目**」。

    此前只测了单个项目的绑定/解绑 —— 那证明不了一个画像能被多个项目共用，
    而这正是 BrandProfile 存在的意义（同一账号下的所有项目共享定位与禁忌）。
    顺带守住 PRD-SCR-004 依赖的前提：跨项目的历史检索靠的就是这层关联。
    """
    deps = _deps(_RecordingAIProvider())
    created = await dispatch(_env("CreateBrandProfile", {"name": "共用画像"}), deps)
    profile_id = created["detail"]["profile"]["id"]

    project_ids = []
    for title in ("项目甲", "项目乙", "项目丙"):
        prj = await dispatch(_env("CreateProject", {"title": title}), deps)
        pid = prj["detail"]["project"]["id"]
        res = await dispatch(
            _env("SetProjectBrandProfile", {"projectId": pid, "brandProfileId": profile_id}),
            deps,
        )
        assert res["ok"] is True, res
        project_ids.append(pid)

    rows = deps.repos.conn.execute(
        "SELECT id FROM content_projects WHERE brand_profile_id=? ORDER BY title",
        (profile_id,),
    ).fetchall()
    assert sorted(r["id"] for r in rows) == sorted(project_ids)

    # 解绑其中一个不应影响其它项目
    await dispatch(
        _env("SetProjectBrandProfile", {"projectId": project_ids[0], "brandProfileId": None}),
        deps,
    )
    still = deps.repos.conn.execute(
        "SELECT COUNT(*) n FROM content_projects WHERE brand_profile_id=?", (profile_id,)
    ).fetchone()["n"]
    assert still == 2


async def test_set_profile_accepts_both_key_spellings() -> None:
    """``profileId`` 与 ``brandProfileId`` 都要认。

    键名写错时旧实现会静默解绑并返回 ok —— 无声的数据丢失。命令名和列名
    都叫 brand_profile_id，调用方写成那个太自然了。
    """
    deps = _deps(_RecordingAIProvider())
    profile_id = (
        await dispatch(_env("CreateBrandProfile", {"name": "别名"}), deps)
    )["detail"]["profile"]["id"]

    for key in ("profileId", "brandProfileId"):
        pid = (await dispatch(_env("CreateProject", {"title": key}), deps))["detail"][
            "project"
        ]["id"]
        res = await dispatch(
            _env("SetProjectBrandProfile", {"projectId": pid, key: profile_id}), deps
        )
        assert res["detail"]["brand_profile_id"] == profile_id, key
        row = deps.repos.conn.execute(
            "SELECT brand_profile_id FROM content_projects WHERE id=?", (pid,)
        ).fetchone()
        assert row["brand_profile_id"] == profile_id, key

    # 显式不给 = 解绑，这条语义要保持
    pid = (await dispatch(_env("CreateProject", {"title": "解绑"}), deps))["detail"][
        "project"
    ]["id"]
    await dispatch(
        _env("SetProjectBrandProfile", {"projectId": pid, "profileId": profile_id}), deps
    )
    await dispatch(_env("SetProjectBrandProfile", {"projectId": pid}), deps)
    row = deps.repos.conn.execute(
        "SELECT brand_profile_id FROM content_projects WHERE id=?", (pid,)
    ).fetchone()
    assert row["brand_profile_id"] is None
