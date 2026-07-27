"""命令幂等测试（PRD §13「重复任务幂等阻止重复输出」）。

command-envelope.schema.json 早就有 idempotencyKey 并写着「Side-effecting
commands SHOULD provide one」，但此前全仓从未消费它——重复提交同一条命令
会重复产出内容版本、重复计费。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from worker.runtime import ingest
from worker.runtime.commands.bus import dispatch
from worker.runtime.commands.idempotency import REPLAY_FLAG
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"

_VALID_ANALYSIS: dict[str, Any] = {
    "summary": "摘要",
    "topics": ["t"],
    "sentiment": "neutral",
    "suggested_title": None,
    "suggested_tags": [],
    "key_points": [],
    "target_audience": None,
    "hook": None,
    "structure": [],
    "risks": [],
    "provider": "fake",
    "model": "fake-1",
    "confidence": 0.5,
}


class _CountingAI:
    """记录被调用次数，用于证明重放没有再次调模型（= 没有重复计费）。"""

    name = "counting-ai"
    model = "count-1"
    estimated_cost_per_1k = 1.0

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls += 1
        return dict(_VALID_ANALYSIS)


class _FailingAI:
    name = "failing-ai"
    model = "fail-1"
    estimated_cost_per_1k = 0.0

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls += 1
        return {"summary": "缺字段"}  # schema 校验必失败


def _deps(ai: Any) -> Deps:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos = Repos(c)
    repos.workspaces.ensure("ws-i")
    return Deps(repos=repos, ingest=ingest, ai=ai)


def _env(key: str | None, command_id: str = "cmd-1") -> dict[str, Any]:
    return {
        "commandId": command_id,
        "commandType": "AnalyzeSource",
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u"},
        "source": "ui",
        "workspaceId": "ws-i",
        "projectId": None,
        "idempotencyKey": key,
        "payload": {"text": "待分析文本"},
        "requestedAt": "2026-07-27T00:00:00+00:00",
    }


async def test_same_key_does_not_produce_duplicate_output() -> None:
    """核心验收：同 key 重复提交不重复产出、不重复调模型。"""
    ai = _CountingAI()
    deps = _deps(ai)

    first = await dispatch(_env("key-1"), deps)
    assert first["ok"] is True, first.get("error")
    second = await dispatch(_env("key-1", command_id="cmd-2"), deps)
    assert second["ok"] is True

    # 模型只被调一次（没有重复计费）
    assert ai.calls == 1
    # 只产出一个内容版本（没有重复输出）
    n = deps.repos.conn.execute(
        "SELECT COUNT(*) n FROM content_versions"
    ).fetchone()["n"]
    assert n == 1
    # 返回同一个 artifact
    assert second["artifact_ids"] == first["artifact_ids"]


async def test_replay_is_marked_and_carries_current_command_id() -> None:
    """调用方要能区分「真跑了」与「这是上次的结果」。"""
    deps = _deps(_CountingAI())
    first = await dispatch(_env("key-2"), deps)
    assert not (first.get("detail") or {}).get(REPLAY_FLAG)

    replay = await dispatch(_env("key-2", command_id="cmd-later"), deps)
    assert replay["detail"][REPLAY_FLAG] is True
    # commandId 用本次的，便于调用方关联自己的请求
    assert replay["commandId"] == "cmd-later"


async def test_different_keys_execute_independently() -> None:
    ai = _CountingAI()
    deps = _deps(ai)
    await dispatch(_env("key-a"), deps)
    await dispatch(_env("key-b"), deps)
    assert ai.calls == 2
    n = deps.repos.conn.execute(
        "SELECT COUNT(*) n FROM content_versions"
    ).fetchone()["n"]
    assert n == 2


async def test_no_key_means_no_dedup() -> None:
    """未提供 key 时保持旧行为（每次都真执行）。"""
    ai = _CountingAI()
    deps = _deps(ai)
    await dispatch(_env(None), deps)
    await dispatch(_env(None, command_id="cmd-2"), deps)
    assert ai.calls == 2


async def test_failure_is_not_cached_so_retry_works() -> None:
    """失败不缓存——否则一次网络抖动就把同一个 key 永久钉死。"""
    failing = _FailingAI()
    deps = _deps(failing)
    bad = await dispatch(_env("key-retry"), deps)
    assert bad["ok"] is False
    assert failing.calls == 1

    # 换成正常 provider，同一个 key 重试应真正执行
    ok_ai = _CountingAI()
    deps.ai = ok_ai
    good = await dispatch(_env("key-retry", command_id="cmd-2"), deps)
    assert good["ok"] is True, good.get("error")
    assert ok_ai.calls == 1


async def test_same_key_across_command_types_is_isolated() -> None:
    """不同命令即便复用同一个 key 也互不干扰。"""
    deps = _deps(_CountingAI())
    analyzed = await dispatch(_env("shared-key"), deps)
    assert analyzed["ok"] is True

    other = _env("shared-key", command_id="cmd-other")
    other["commandType"] = "ListProjects"
    other["payload"] = {}
    listed = await dispatch(other, deps)
    assert listed["ok"] is True
    # 不该拿到 AnalyzeSource 的缓存结果
    assert not (listed.get("detail") or {}).get(REPLAY_FLAG)
    assert "projects" in (listed.get("detail") or {})
