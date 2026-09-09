"""S4 创作者风格测试：六维 style_dna + 禁用词零出现。

锁死：

1. ``styleDna``（六维 Creator DNA）持久化与出参往返、畸形拒绝、兼容旧行
   （无 style_dna 的行读出空 dict，不炸）；
2. prompt 注入逐维硬约束（``format_brand_prompt_block``），且只在有值时才注入；
3. **禁用词零出现**（S4 验收的断言测试）：GenerateScript 产出命中 →
   自动重试一次 → 成功那次必须是干净的；两次都命中 → 任务 FAILED 且错误
   **指名命中了哪些词** —— 绝不静默放行违规文本、也不悄悄改写。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.handlers.brand import collect_banned_hits, format_brand_prompt_block
from worker.runtime.models import (
    ContentVersion,
    JobState,
)

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"

#: 与 douyin-ego-creator 蒸馏口径一致的六维
_FULL_DNA: dict[str, str] = {
    "contentStrategy": "反常识断言开场，垂直女性成长",
    "hookDna": "前三秒抛出反常识结论",
    "narrativeDna": "是什么→为什么→怎么办三段递进",
    "explosionDna": "每 15 秒一个爆点转折",
    "languageDna": "短句口语化，少用连接词",
    "conversionDna": "结尾一个行动召唤",
}


class _SeqAI:
    """按调用次序返回预设产出的 Fake AI（测重试）。"""

    name = "fake-ai"
    model = "fake-1"
    estimated_cost_per_1k = 0.0

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []
        self.calls = 0

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.prompts.append(prompt)
        out = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return dict(out)


def _deps(ai: Any = None) -> Deps:
    conn = in_memory()
    run_migrations(conn, _MIG_DIR)
    return Deps(repos=Repos(conn), ingest=ingest, ai=ai)


def _env(
    command_type: str,
    payload: dict[str, Any] | None = None,
    workspace_id: str = "ws-dna",
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


async def _new_profile(deps: Deps, **extra: Any) -> str:
    payload: dict[str, Any] = {
        "name": "创作者甲",
        "styleDna": dict(_FULL_DNA),
        "bannedExpressions": ["首先", "综上所述"],
        **extra,
    }
    res = await dispatch(_env("CreateBrandProfile", payload), deps)
    assert res["ok"] is True, res
    return str(res["detail"]["profile"]["id"])


# ----- styleDna 持久化与校验 -----


async def test_create_profile_roundtrips_style_dna() -> None:
    deps = _deps()
    res = await dispatch(
        _env("CreateBrandProfile", {"name": "甲", "styleDna": dict(_FULL_DNA)}),
        deps,
    )
    assert res["ok"] is True, res
    profile = res["detail"]["profile"]
    assert profile["styleDna"] == _FULL_DNA


async def test_update_profile_style_dna() -> None:
    deps = _deps()
    pid = await _new_profile(deps)
    res = await dispatch(
        _env("UpdateBrandProfile", {"profileId": pid, "styleDna": {"hookDna": "改了"}}),
        deps,
    )
    assert res["ok"] is True, res
    assert res["detail"]["profile"]["styleDna"] == {"hookDna": "改了"}


async def test_style_dna_must_be_object_of_strings() -> None:
    deps = _deps()
    for bad in (["a", "b"], {"k": 1}, "nope", 3):
        res = await dispatch(
            _env("CreateBrandProfile", {"name": "x", "styleDna": bad}), deps
        )
        assert res["ok"] is False, bad
        assert "INVALID_ARGUMENT" in res["error"]


async def test_list_profiles_includes_style_dna_and_tolerates_legacy_rows() -> None:
    """直接插一行没有 style_dna 的旧数据 → 读出空 dict，不炸。"""
    deps = _deps()
    deps.repos.workspaces.ensure("ws-dna")
    conn = deps.repos.conn
    conn.execute(
        "INSERT INTO brand_profiles "
        "(id, workspace_id, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("bp_legacy", "ws-dna", "旧画像", "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
    )
    conn.commit()
    res = await dispatch(_env("ListBrandProfiles"), deps)
    assert res["ok"] is True, res
    by_name = {p["name"]: p for p in res["detail"]["profiles"]}
    assert by_name["旧画像"]["styleDna"] == {}


# ----- prompt 注入 + 命中收集 -----


def test_format_block_injects_six_dims() -> None:
    block = format_brand_prompt_block(
        {"name": "甲", "styleDna": _FULL_DNA, "bannedExpressions": ["首先"]}
    )
    # 六个维度的中文标签都在，且带上各自的约束值
    assert "内容策略（选题/受众/价值密度）" in block
    assert "开头钩子（Hook）" in block
    assert "叙事结构（Narrative）" in block
    assert "爆点设计（Explosion）" in block
    assert "语言风格（Language）" in block
    assert "转化引导（Conversion）" in block
    assert "反常识断言开场" in block
    assert "不得使用以下表达：首先" in block


def test_format_block_skips_when_dna_empty() -> None:
    block = format_brand_prompt_block({"name": "甲"})
    assert "风格 DNA" not in block


def test_collect_banned_hits() -> None:
    assert collect_banned_hits("今天首先我想说，然后综上所述", ["首先", "综上所述"]) == [
        "首先",
        "综上所述",
    ]
    # 去重（按出现序）、大小写不敏感、空输入安全
    assert collect_banned_hits("首先…首先…", ["首先"]) == ["首先"]
    assert collect_banned_hits("Hello World", ["hello"]) == ["hello"]
    assert collect_banned_hits("", ["a"]) == []
    assert collect_banned_hits("正文", []) == []
    assert collect_banned_hits("正文", ["", "  "]) == []


# ----- GenerateScript 禁用词零出现 -----


def _script_version(deps: Deps, project_id: str) -> str:
    return deps.repos.content_versions.insert(
        ContentVersion(
            project_id=project_id,
            content_type="topic_proposal",
            content=json.dumps(
                {"angles": [{"id": "a1", "title": "t", "rationale": "r", "hook": "h"}]}
            ),
            content_hash="hp",
            producer={},
        )
    )


async def _project(deps: Deps) -> str:
    deps.repos.workspaces.ensure("ws-dna")
    return deps.repos.projects.get_or_create_default("ws-dna").id


async def test_generate_script_retries_on_banned_then_succeeds() -> None:
    deps = _deps(
        _SeqAI(
            [
                {"title": "首先的标题", "body": "综上所述这不行。"},
                {"title": "干净标题", "body": "这次干净了。"},
            ]
        )
    )
    prj = await _project(deps)
    pid = await _new_profile(deps)
    await dispatch(_env("SetProjectBrandProfile", {"projectId": prj, "profileId": pid}), deps)
    prop = _script_version(deps, prj)

    res = await dispatch(
        _env("GenerateScript", {"proposal_version_id": prop, "topic_id": "a1"}, project_id=prj),
        deps,
    )
    assert res["ok"] is True, res
    # 两次 AI 调用：第一次违规被拦，第二次干净放行
    assert deps.ai.calls == 2
    assert res["detail"]["bannedRetried"] is True
    # 落库内容必须是干净的那版 —— 不是首版违规文本
    cv = deps.repos.content_versions.get(res["artifact_ids"][0])
    assert cv is not None
    content = json.loads(cv.content)
    assert "首先" not in content["title"] and "综上所述" not in content["body"]


async def test_generate_script_fails_when_banned_persists() -> None:
    deps = _deps(
        _SeqAI(
            [
                {"title": "首先", "body": "综上所述"},
                {"title": "还是首先", "body": "还是综上所述"},
            ]
        )
    )
    prj = await _project(deps)
    pid = await _new_profile(deps)
    await dispatch(_env("SetProjectBrandProfile", {"projectId": prj, "profileId": pid}), deps)
    prop = _script_version(deps, prj)

    res = await dispatch(
        _env("GenerateScript", {"proposal_version_id": prop, "topic_id": "a1"}, project_id=prj),
        deps,
    )
    assert res["ok"] is False
    # 错误必须指名命中了哪些词（不是一句笼统 failed）
    assert "首先" in res["error"] and "综上所述" in res["error"]
    # 任务真的 FAILED（不是卡 RUNNING）
    job = deps.repos.jobs.get(res["job_id"])
    assert job is not None and job.state == JobState.FAILED
    # 违规文本**不得落库**
    rows = deps.repos.conn.execute(
        "SELECT COUNT(*) AS n FROM content_versions WHERE content_type='script'"
    ).fetchone()
    assert rows["n"] == 0


async def test_generate_script_without_profile_never_checks() -> None:
    """无画像（use_brand_profile 缺省 True 但未关联）→ 不重试不误伤。"""
    deps = _deps(_SeqAI([{"title": "任何标题", "body": "含“首先”也没人管"}]))
    prj = await _project(deps)
    prop = _script_version(deps, prj)
    res = await dispatch(
        _env("GenerateScript", {"proposal_version_id": prop, "topic_id": "a1"}, project_id=prj),
        deps,
    )
    assert res["ok"] is True, res
    assert deps.ai.calls == 1
    assert res["detail"]["bannedRetried"] is False
