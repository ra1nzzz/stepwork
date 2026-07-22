"""GetConfig / UpdateConfig 命令测试（SET.5 设置页）。

守卫：
- GetConfig 返回合并后的默认配置，掩码不含明文密钥。
- UpdateConfig 把**非密钥**落 ``Workspace.settings``，密钥**绝不**入 SQLite，
  仅进入进程内存覆盖层 ``CONFIG_OVERRIDES``。
- 配置类命令对非法 actor（如 agent）干净拒绝（FORBIDDEN_ACTOR）。
- 经覆盖层注入的密钥能真正驱动 ``resolve_ai/asr/tts``（且不污染进程 env）。
- 部分保存（只改某 section）不得清空其它 section 已存密钥（qa P0）。
"""

from __future__ import annotations

import json
import os
from typing import Any

from worker.runtime.bootstrap import MIGRATIONS_DIR
from worker.runtime.db.connection import in_memory
from worker.runtime.db.migrations import run_migrations
from worker.runtime.handlers import commands
from worker.runtime.providers.resolve import (
    CONFIG_OVERRIDES,
    resolve_ai,
    resolve_asr,
    resolve_tts,
)
from worker.runtime.state import WorkerState


def _state() -> WorkerState:
    s = WorkerState()
    conn = in_memory()
    run_migrations(conn, MIGRATIONS_DIR)
    s.db_conn = conn
    return s


def _env(
    ws_id: str,
    command_type: str,
    payload: dict[str, Any] | None = None,
    actor_type: str = "desktop",
) -> dict[str, Any]:
    return {
        "commandId": "c",
        "commandType": command_type,
        "schemaVersion": "1",
        "actor": {"type": actor_type, "id": "ui"},
        "source": "desktop",
        "workspaceId": ws_id,
        "requestedAt": "t",
        "payload": payload or {},
    }


async def test_get_config_returns_defaults() -> None:
    s = _state()
    ret = await commands.handle_command({"envelope": _env("ws1", "GetConfig")}, s)
    res = ret["result"]
    assert res["ok"] is True
    cfg = res["detail"]["config"]
    assert cfg["llm"]["provider"] == "cloud"
    assert cfg["workspace"]["defaultPath"] == ""
    # 默认无密钥：掩码后 apiKey 为空串（不是明文）
    assert cfg["llm"]["apiKey"] == ""


async def test_update_config_persists_non_secret_and_masks_secret() -> None:
    s = _state()
    settings: dict[str, Any] = {
        "llm": {
            "provider": "cloud",
            "model": "gpt-4o",
            "apiKey": "sk-secret-123",
            "baseUrl": "https://ai.x/v1",
            "costPer1k": "0.01",
            "sampling": {"temperature": 0.5, "topP": 0.8, "maxTokens": 1024},
        },
        "asr": {"provider": "cloud", "apiKey": "asr-k", "baseUrl": "https://asr.x"},
        "tts": {"provider": "cloud", "apiKey": "tts-k", "baseUrl": "https://tts.x", "model": "m1"},
        "workspace": {"defaultPath": "/tmp/x"},
        "brand": {
            "name": "n", "audience": "a", "tone": "t",
            "mustExecute": [], "defaultOutput": [],
        },
        "data": {
            "retentionDays": 7, "desensitize": True,
            "projectDelete": False, "uploadScope": "",
        },
        "export": {"format": "SRT", "checkDeps": False},
        "ui": {"theme": "light", "language": "en", "logLevel": "debug"},
    }
    ret = await commands.handle_command(
        {"envelope": _env("ws2", "UpdateConfig", settings)}, s
    )
    assert ret["result"]["ok"] is True

    # DB 不应含明文密钥（剥离后连 key 都不存在）
    row = s.db_conn.execute(
        "SELECT settings FROM workspaces WHERE id=?", ("ws2",)
    ).fetchone()
    saved = json.loads(row["settings"])
    assert "apiKey" not in saved["llm"]  # 密钥被彻底剥离
    assert saved["llm"]["model"] == "gpt-4o"
    assert saved["workspace"]["defaultPath"] == "/tmp/x"

    # 覆盖层应持有密钥
    assert CONFIG_OVERRIDES["ws2"]["llm"]["apiKey"] == "sk-secret-123"

    # GetConfig 回显掩码 + hasKey
    ret2 = await commands.handle_command({"envelope": _env("ws2", "GetConfig")}, s)
    res2 = ret2["result"]
    assert res2["ok"] is True
    cfg = res2["detail"]["config"]
    assert cfg["llm"]["apiKey"] == "••••"
    assert "apiKey" not in saved["llm"]
    assert res2["detail"]["resolved"]["ai"]["hasKey"] is True
    assert res2["detail"]["resolved"]["ai"]["model"] == "gpt-4o"


async def test_update_config_forbidden_actor() -> None:
    s = _state()
    env = _env("ws3", "UpdateConfig", {"llm": {}}, actor_type="agent")
    ret = await commands.handle_command({"envelope": env}, s)
    assert ret["result"]["ok"] is False
    assert "FORBIDDEN_ACTOR" in ret["result"]["error"]


async def test_resolve_ai_uses_override_without_env() -> None:
    s = _state()
    for k in (
        "STEPWORK_AI_PROVIDER",
        "STEPWORK_AI_API_KEY",
        "STEPWORK_AI_BASE_URL",
        "STEPWORK_AI_MODEL",
    ):
        os.environ.pop(k, None)

    settings: dict[str, Any] = {
        "llm": {"provider": "cloud", "model": "gpt-4o", "apiKey": "sk-x", "baseUrl": "https://ai.x/v1"},
        "asr": {"provider": "cloud", "apiKey": "", "baseUrl": ""},
        "tts": {"provider": "cloud", "apiKey": "", "baseUrl": "", "model": "m"},
        "workspace": {"defaultPath": ""},
        "brand": {
            "name": "n", "audience": "a", "tone": "t",
            "mustExecute": [], "defaultOutput": [],
        },
        "data": {
            "retentionDays": 30, "desensitize": True,
            "projectDelete": False, "uploadScope": "",
        },
        "export": {"format": "MP4", "checkDeps": True},
        "ui": {"theme": "dark", "language": "zh-CN", "logLevel": "info"},
    }
    os.environ["STEPWORK_AI_PROVIDER"] = "cloud"
    await commands.handle_command(
        {"envelope": _env("ws4", "UpdateConfig", settings)}, s
    )
    ai = resolve_ai("ws4")
    assert ai is not None
    # 密钥经覆盖层注入，未落到进程 env
    assert os.environ.get("STEPWORK_AI_API_KEY") is None


def _full_settings(**over: Any) -> dict[str, Any]:
    """构造完整 8-section 设置 payload（带默认值，便于局部覆盖）。"""
    base: dict[str, Any] = {
        "llm": {
            "provider": "cloud", "model": "m", "apiKey": "",
            "baseUrl": "", "costPer1k": "0.01",
            "sampling": {"temperature": 0.5, "topP": 0.8, "maxTokens": 1024},
        },
        "asr": {"provider": "cloud", "apiKey": "", "baseUrl": ""},
        "tts": {"provider": "cloud", "apiKey": "", "baseUrl": "", "model": "m"},
        "workspace": {"defaultPath": ""},
        "brand": {
            "name": "n", "audience": "a", "tone": "t",
            "mustExecute": [], "defaultOutput": [],
        },
        "data": {
            "retentionDays": 30, "desensitize": True,
            "projectDelete": False, "uploadScope": "",
        },
        "export": {"format": "MP4", "checkDeps": True},
        "ui": {"theme": "dark", "language": "zh-CN", "logLevel": "info"},
    }
    base.update(over)
    return base


async def test_update_config_merges_sections_preserves_keys() -> None:
    """部分保存（只改 LLM、ASR/TTS 传空 dict）不得清空已存密钥（qa P0）。"""
    s = _state()
    seed = _full_settings(
        asr={"provider": "cloud", "apiKey": "asr-seed", "baseUrl": "https://asr.seed"},
        tts={"provider": "cloud", "apiKey": "tts-seed",
              "baseUrl": "https://tts.seed", "model": "m"},
    )
    await commands.handle_command({"envelope": _env("wsm", "UpdateConfig", seed)}, s)

    # 只更新 llm；asr/tts 传空 dict（模拟「重载后仅改了 LLM 保存」）
    llm_only = _full_settings(
        llm={"provider": "cloud", "model": "gpt-4o", "apiKey": "llm-new",
              "baseUrl": "https://ai.x/v1", "costPer1k": "0.01",
              "sampling": {"temperature": 0.5, "topP": 0.8, "maxTokens": 1024}},
        asr={}, tts={},
    )
    await commands.handle_command(
        {"envelope": _env("wsm", "UpdateConfig", llm_only)}, s
    )
    ov = CONFIG_OVERRIDES["wsm"]
    assert ov["asr"]["apiKey"] == "asr-seed"   # 已存密钥被保留
    assert ov["tts"]["apiKey"] == "tts-seed"
    assert ov["llm"]["apiKey"] == "llm-new"


async def test_resolve_asr_uses_override_without_env() -> None:
    s = _state()
    for k in ("STEPWORK_ASR_PROVIDER", "STEPWORK_ASR_API_KEY", "STEPWORK_ASR_BASE_URL"):
        os.environ.pop(k, None)
    os.environ["STEPWORK_ASR_PROVIDER"] = "cloud"
    settings = _full_settings(
        asr={"provider": "cloud", "apiKey": "asr-x", "baseUrl": "https://asr.x"},
    )
    await commands.handle_command(
        {"envelope": _env("wsa", "UpdateConfig", settings)}, s
    )
    asr = resolve_asr("wsa")
    assert asr is not None
    assert os.environ.get("STEPWORK_ASR_API_KEY") is None


async def test_resolve_tts_uses_override_without_env() -> None:
    s = _state()
    for k in ("STEPWORK_TTS_PROVIDER", "STEPWORK_TTS_API_KEY", "STEPWORK_TTS_BASE_URL"):
        os.environ.pop(k, None)
    os.environ["STEPWORK_TTS_PROVIDER"] = "cloud"
    settings = _full_settings(
        tts={"provider": "cloud", "apiKey": "tts-x",
              "baseUrl": "https://tts.x", "model": "m1"},
    )
    await commands.handle_command(
        {"envelope": _env("wst", "UpdateConfig", settings)}, s
    )
    tts = resolve_tts("wst")
    assert tts is not None
    assert os.environ.get("STEPWORK_TTS_API_KEY") is None
