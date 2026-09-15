"""第九轮：PROVIDER_REGISTRY 表驱动锁行为。

对应评审报告 dimension C **P0 #1** —— ``providers/resolve.py`` 里
ASR/AI/TTS/Renderer/Publish 五类 provider 各写了一段 ``if kind == ...``
链，新增一个 provider 必须**改这个 549 行的核心模块**，与开闭原则相反；
本批收口成 5 张 :data:`*_FACTORIES` 表，``resolve_*`` 只剩三行代码。

新加一个 provider 的正确姿势（本文件同时是操作示范）：
    1. 在 ``worker/runtime/providers/<domain>/`` 下实现 Provider；
    2. 在 ``resolve.py`` 末尾加一个 ``_xxx_factory(...)`` 小函数；
    3. 在对应的 ``*_FACTORIES`` 里加 ``"my-kind": _xxx_factory``；
    4. 完 —— 不用改 ``resolve_asr/ai/tts/renderer/publish`` 任何一行。

锁死"新代码不再往 ``resolve_*`` 函数体里塞 if 分支"这一条纪律。
"""

from __future__ import annotations

import ast
from pathlib import Path

_RESOLVE = Path("worker/runtime/providers/resolve.py")


def test_factories_are_declared() -> None:
    """五张工厂表必须真的在文件里（表驱动是本轮的核心承诺）。"""
    src = _RESOLVE.read_text(encoding="utf-8")
    for table in (
        "ASR_FACTORIES",
        "AI_FACTORIES",
        "AI_HINT_FACTORIES",
        "TTS_FACTORIES",
        "RENDERER_FACTORIES",
        "PUBLISH_FACTORIES",
    ):
        assert f"{table}: dict" in src, (
            f"{table} 未声明 —— 表驱动没落地，未来加 provider 又要改 resolve_*"
        )


def test_resolve_functions_are_thin_dispatchers() -> None:
    """``resolve_asr`` / ``resolve_ai`` / ``resolve_tts`` /
    ``_build_renderer`` / ``resolve_publish_provider`` 函数体里**不能再
    出现 if kind == "xxx" 分支** —— 表驱动的本意就是把这些分支挪出去。
    """
    tree = ast.parse(_RESOLVE.read_text(encoding="utf-8"))
    targets = {
        "resolve_asr",
        "resolve_ai",
        "resolve_tts",
        "_build_renderer",
        "resolve_publish_provider",
        "ai_provider_from_hint",
    }
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in targets:
            continue
        for inner in ast.walk(node):
            # 找 kind == "..." 的字符串比较
            if (
                isinstance(inner, ast.Compare)
                and isinstance(inner.left, ast.Name)
                and inner.left.id == "kind"
                and any(
                    isinstance(c, ast.Constant) and isinstance(c.value, str)
                    for c in inner.comparators
                )
            ):
                offenders.append(f"{node.name}:{inner.lineno}")
    assert offenders == [], (
        f"resolve 分发器里又出现了 ``if kind == ...`` 分支：{offenders} —— "
        "加 provider 应该只改 *_FACTORIES 表，不该动这些函数体"
    )


def test_factory_tables_cover_all_documented_kinds() -> None:
    """每张表至少要有原本 if-chain 覆盖的所有 kind（含别名）——
    防止表驱动"重写时漏一个别名"，让老用户 ``STEPWORK_ASR_PROVIDER=
    faster-whisper`` 突然失效。"""
    from worker.runtime.providers import resolve as R

    # 与文档 + 修前 if-chain 完全一致的 kind 集
    assert set(R.ASR_FACTORIES) >= {
        "auto", "local", "whisper", "faster-whisper", "faster_whisper", "cloud",
    }
    assert set(R.AI_FACTORIES) >= {
        "cloud", "openai-compatible", "openai_compatible", "ollama",
    }
    assert set(R.AI_HINT_FACTORIES) >= {
        "cloud", "openai-compatible", "openai_compatible", "ollama",
    }
    assert set(R.TTS_FACTORIES) >= {
        "local", "edge", "edge-tts", "edge_tts",
        "stepfun", "stepfun-tts", "stepfun_tts", "cloud",
    }
    assert set(R.RENDERER_FACTORIES) >= {"playwright", "pw", "ffmpeg", ""}
    assert set(R.PUBLISH_FACTORIES) >= {"opencli"}


def test_unknown_kind_returns_none() -> None:
    """未知 kind 必须回 None（→ handler 转 UNAVAILABLE）—— 拼错的
    渠道名不该"凑合跑"，与修前每条 if-chain 末尾的 ``return None`` 一致。"""
    # 用一个明显不存在的 provider kind
    import os

    from worker.runtime.providers import resolve as R

    os.environ["STEPWORK_ASR_PROVIDER"] = "definitely-not-a-real-asr"
    os.environ["STEPWORK_AI_PROVIDER"] = "definitely-not-a-real-ai"
    os.environ["STEPWORK_TTS_PROVIDER"] = "definitely-not-a-real-tts"
    os.environ["STEPWORK_RENDER_PROVIDER"] = "definitely-not-a-real-renderer"
    os.environ["STEPWORK_PUBLISH_PROVIDER"] = "definitely-not-a-real-publish"
    try:
        assert R.resolve_asr("ws-x") is None
        assert R.resolve_ai("ws-x") is None
        assert R.resolve_tts("ws-x") is None
        assert R.resolve_renderer() is None
        assert R.resolve_publish_provider() is None
    finally:
        for k in (
            "STEPWORK_ASR_PROVIDER", "STEPWORK_AI_PROVIDER",
            "STEPWORK_TTS_PROVIDER", "STEPWORK_RENDER_PROVIDER",
            "STEPWORK_PUBLISH_PROVIDER",
        ):
            os.environ.pop(k, None)


def test_default_kinds_still_work() -> None:
    """默认 kind（ASR=auto / TTS=local）在没设 env 时行为与修前一致。"""
    import os

    from worker.runtime.providers import resolve as R
    from worker.runtime.providers.asr.local import LocalASRProvider
    from worker.runtime.providers.tts.local import LocalTTSProvider

    os.environ.pop("STEPWORK_ASR_PROVIDER", None)
    os.environ.pop("STEPWORK_TTS_PROVIDER", None)
    # 修前：auto → whisper 装了用真引擎 / 没装回落 LocalASRProvider。
    # 本机 CI 通常没装 faster_whisper，走后者。
    asr = R.resolve_asr("ws-x")
    assert isinstance(asr, LocalASRProvider), (
        f"auto 默认路径变了：拿到 {type(asr).__name__}"
    )
    tts = R.resolve_tts("ws-x")
    assert isinstance(tts, LocalTTSProvider)


def test_hint_path_shares_validators_with_env_path() -> None:
    """per-request hint 与 env 装配共享 _ai_build_* 构造函数：
    cloud 必须有 key+url，openai-compatible 只要 url —— 这条**协议语义
    差异**不能因为重构被抹平。"""
    from worker.runtime.providers import resolve as R
    from worker.runtime.providers.ai.openai_compatible import (
        OpenAICompatibleProvider,
    )

    # cloud 缺 key → 拒（与 env 路径同规则）
    assert R.ai_provider_from_hint(
        {"kind": "cloud", "base_url": "https://x", "model": "m"}
    ) is None
    # cloud 有 key + url → 通过
    assert R.ai_provider_from_hint(
        {"kind": "cloud", "base_url": "https://x", "api_key": "k", "model": "m"}
    ) is not None
    # openai-compat 只要 url（ollama 常态无 key）
    got = R.ai_provider_from_hint(
        {"kind": "ollama", "base_url": "http://localhost:11434"}
    )
    assert isinstance(got, OpenAICompatibleProvider)
