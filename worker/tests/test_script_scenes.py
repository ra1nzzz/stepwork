"""S2 分幕派生测试（脚本 → ``video_scenes``）。

锁死两件事：

1. **切分是纯函数且永不产生垃圾幕**（空正文、纯标点、``3.5`` 这种小数点
   都不能切出幕来）—— 幕是 TTS 的最小调用单位，垃圾幕 = 垃圾音频。
2. **三条写入路径都真的落库**（``GenerateScript`` / ``SaveScript`` /
   ``EditParagraph``）—— 只要有一条漏了，分幕就退化成手工维护的孤岛，
   下游配音/配图/渲染拿不到输入。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from worker.runtime.commands.bus import dispatch
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import ContentVersion
from worker.runtime.script.segment import (
    DEFAULT_MAX_CHARS,
    plain_text,
    segment_scenes,
    split_sentences,
)

_MIG_DIR = Path(__file__).resolve().parents[2] / "migrations"


# ----- 纯函数：切分规则 -----


def test_empty_body_yields_no_scenes() -> None:
    assert segment_scenes("") == []
    assert segment_scenes("   \n\n  \n\n") == []


def test_short_paragraph_is_one_scene() -> None:
    assert segment_scenes("今天聊一个反常识的事。") == ["今天聊一个反常识的事。"]


def test_blank_line_is_scene_boundary() -> None:
    body = "第一幕。\n\n第二幕。\n\n第三幕。"
    assert segment_scenes(body) == ["第一幕。", "第二幕。", "第三幕。"]


def test_long_paragraph_splits_by_sentence() -> None:
    body = "。" .join(f"这是第{i}句话" for i in range(1, 7)) + "。"
    scenes = segment_scenes(body, max_chars=12)
    assert len(scenes) > 1
    # 每句 9 字左右，打包后单幕不超过 max_chars + 一句的长度
    for s in scenes:
        assert len(s) <= 12 + 12
    # 拼接回去必须等于原文（不丢字、不加字）
    assert "".join(scenes) == body


def test_overlong_sentence_stays_whole() -> None:
    """绝不在句子中间劈开：幕是 TTS 最小调用单位，劈开就是断字。"""
    long_one = "一" * 200 + "。"
    assert segment_scenes(long_one, max_chars=36) == [long_one]


def test_max_scenes_is_a_hard_cap() -> None:
    body = "\n\n".join(f"第{i}段。" for i in range(50))
    assert len(segment_scenes(body, max_scenes=5)) == 5


def test_invalid_limits_rejected() -> None:
    with pytest.raises(ValueError):
        segment_scenes("abc", max_chars=0)
    with pytest.raises(ValueError):
        segment_scenes("abc", max_scenes=0)


def test_ellipsis_does_not_become_its_own_scene() -> None:
    """``……`` 会被正则切成两个 ``…``，不能各成一幕。"""
    assert segment_scenes("他说到一半就停了……") == ["他说到一半就停了……"]
    assert split_sentences("……") == []


def test_ascii_period_is_not_a_boundary() -> None:
    """``3.5`` / ``AI.`` 不该被切开（句读集合刻意不含 ASCII 句点）。"""
    assert segment_scenes("提速 3.5 倍，成本降到一半。") == [
        "提速 3.5 倍，成本降到一半。"
    ]


def test_max_chars_default_is_positive() -> None:
    assert DEFAULT_MAX_CHARS > 0


# ----- 纯函数：plain_text（三种输入形态） -----


def test_plain_text_passthrough() -> None:
    assert plain_text("纯文本正文") == "纯文本正文"


def test_plain_text_from_title_body_json() -> None:
    assert plain_text(json.dumps({"title": "T", "body": "正文在这儿"})) == "正文在这儿"
    assert plain_text({"title": "T", "body": "正文在这儿"}) == "正文在这儿"


def test_plain_text_flattens_tiptap_doc() -> None:
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "第一段。"}],
            },
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "第二段。"}],
            },
        ],
    }
    text = plain_text(doc)
    # 块级节点之间补空行 → 段落边界保住了，切得出两幕
    assert segment_scenes(text) == ["第一段。", "第二段。"]


def test_plain_text_of_plain_json_string_is_not_mangled() -> None:
    """纯文本别被 json 解析误伤（只有 { / [ 开头才尝试解析）。"""
    assert plain_text("123") == "123"
    assert plain_text("{坏 JSON") == "{坏 JSON"


# ----- 经 Command Bus：三条写入路径 -----


class _FakeAI:
    name = "fake-ai"
    model = "fake-model"
    estimated_cost_per_1k = 0.0

    def __init__(self, body: str | None = None) -> None:
        # 用 ``is not None`` 而非 ``or``：空正文是合法用例，会被 ``or`` 吞掉
        self._body = body

    async def complete(self, prompt: str, schema: Any = None) -> dict[str, Any]:
        return {
            "angles": [{"id": "a1", "title": "t", "rationale": "r", "hook": "h"}],
            "title": "脚本标题",
            "body": (
                self._body
                if self._body is not None
                else "第一段。\n\n第二段。\n\n第三段。"
            ),
        }


class _FakeParagraphAI:
    name = "fake-ai"
    model = "fake-model"
    estimated_cost_per_1k = 0.0

    async def complete(self, prompt: str, schema: Any = None) -> dict[str, Any]:
        return {"text": "改写后的段落，稍微长一点凑够字数。"}


def _deps(ai: Any = None) -> Deps:
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    return Deps(repos=Repos(c), ai=ai or _FakeAI())


def _env(command_type: str, payload: dict[str, Any], ws: str = "ws-s") -> dict[str, Any]:
    return {
        "commandId": "cmd-s",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": "user", "id": "u1"},
        "source": "ui",
        "workspaceId": ws,
        "payload": payload,
        "requestedAt": "2026-09-08T00:00:00+00:00",
    }


def _proposal(deps: Deps) -> str:
    deps.repos.workspaces.ensure("ws-s")
    pid = deps.repos.projects.get_or_create_default("ws-s").id
    return deps.repos.content_versions.insert(
        ContentVersion(
            project_id=pid,
            content_type="topic_proposal",
            content=json.dumps(
                {"angles": [{"id": "a1", "title": "t", "rationale": "r", "hook": "h"}]}
            ),
            content_hash="hp",
            producer={},
        )
    )


async def test_generate_script_persists_scenes() -> None:
    deps = _deps()
    prop = _proposal(deps)
    res = await dispatch(
        _env("GenerateScript", {"proposal_version_id": prop, "topic_id": "a1"}),
        deps,
    )
    assert res["ok"] is True, res.get("error")
    version_id = res["artifact_ids"][0]

    scenes = deps.repos.video_scenes.list_by_version(version_id)
    assert [s.text for s in scenes] == ["第一段。", "第二段。", "第三段。"]
    assert [s.seq for s in scenes] == [0, 1, 2]
    # detail 带 id，前端可直接拿去排队配音/配图
    assert res["detail"]["sceneCount"] == 3
    assert res["detail"]["sceneIds"] == [s.id for s in scenes]
    # duration 是 TTS 实测值，此刻必须是 0 —— 不是估值
    assert all(s.duration_sec == 0.0 for s in scenes)


async def test_generate_script_empty_body_leaves_no_scenes() -> None:
    deps = _deps(_FakeAI(body=""))
    prop = _proposal(deps)
    res = await dispatch(
        _env("GenerateScript", {"proposal_version_id": prop, "topic_id": "a1"}),
        deps,
    )
    assert res["ok"] is True, res.get("error")
    version_id = res["artifact_ids"][0]
    assert deps.repos.video_scenes.list_by_version(version_id) == []
    assert res["detail"]["sceneCount"] == 0


async def test_each_new_version_gets_its_own_scenes() -> None:
    """新版本 = 新的一批幕；同一版本重复派生不得叠加出重复行。"""
    deps = _deps()
    prop = _proposal(deps)
    r1 = await dispatch(
        _env("GenerateScript", {"proposal_version_id": prop, "topic_id": "a1"}),
        deps,
    )
    r2 = await dispatch(
        _env("GenerateScript", {"proposal_version_id": prop, "topic_id": "a1"}),
        deps,
    )
    v1, v2 = r1["artifact_ids"][0], r2["artifact_ids"][0]
    assert v1 != v2
    s1 = deps.repos.video_scenes.list_by_version(v1)
    s2 = deps.repos.video_scenes.list_by_version(v2)
    assert len(s1) == 3 and len(s2) == 3
    # 幕 id 不共享；seq 各自从 0 开始
    assert {s.id for s in s1}.isdisjoint({s.id for s in s2})
    assert [s.seq for s in s2] == [0, 1, 2]


async def test_save_script_derives_scenes_from_tiptap() -> None:
    deps = _deps()
    deps.repos.workspaces.ensure("ws-s")
    doc = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "第一幕。"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "第二幕。"}]},
        ],
    }
    res = await dispatch(_env("SaveScript", {"content": doc}), deps)
    assert res["ok"] is True, res.get("error")
    version_id = res["artifact_ids"][0]
    scenes = deps.repos.video_scenes.list_by_version(version_id)
    assert [s.text for s in scenes] == ["第一幕。", "第二幕。"]
    assert res["detail"]["sceneCount"] == 2


async def test_save_script_plain_text_body() -> None:
    deps = _deps()
    deps.repos.workspaces.ensure("ws-s")
    res = await dispatch(
        _env("SaveScript", {"content": "只有一段的稿子，没有空行。"}), deps
    )
    assert res["ok"] is True, res.get("error")
    scenes = deps.repos.video_scenes.list_by_version(res["artifact_ids"][0])
    assert [s.text for s in scenes] == ["只有一段的稿子，没有空行。"]


async def test_edit_paragraph_rederives_scenes() -> None:
    """段落被改写后整篇重派生：段数可能变了，沿用旧幕会错位。"""
    c = in_memory()
    run_migrations(c, _MIG_DIR)
    repos = Repos(c)
    ws = "ws-e"
    repos.workspaces.ensure(ws)
    pid = repos.projects.get_or_create_default(ws).id
    src = repos.content_versions.insert(
        ContentVersion(
            project_id=pid,
            content_type="script",
            content="第一段内容。\n\n第二段内容。\n\n第三段内容。",
            content_hash="h",
            producer={},
        )
    )
    deps = Deps(repos=repos, ai=_FakeParagraphAI())
    res = await dispatch(
        {
            "commandId": "cmd-e",
            "commandType": "EditParagraph",
            "schemaVersion": "1",
            "actor": {"type": "user", "id": "u"},
            "source": "ui",
            "workspaceId": ws,
            "projectId": pid,
            "payload": {"version_id": src, "paragraph_index": 1, "operation": "expand"},
            "requestedAt": "2026-09-08T00:00:00+00:00",
        },
        deps,
    )
    assert res["ok"] is True, res.get("error")
    new_id = res["artifact_ids"][0]
    scenes = repos.video_scenes.list_by_version(new_id)
    # 源版本不被动（新版本是新版本）
    assert repos.video_scenes.list_by_version(src) == []
    assert len(scenes) == 3
    assert scenes[1].text == "改写后的段落，稍微长一点凑够字数。"
    assert res["detail"]["sceneCount"] == 3
