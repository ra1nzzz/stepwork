"""``GetConfig`` / ``UpdateConfig`` 命令处理（SET.5 设置页）。

职责：
- ``GetConfig``：合并 ``defaults < env < Workspace.settings < 密钥覆盖层``，
  返回**掩码**后的完整配置 + 解析摘要（provider / model / 是否持有密钥）。
- ``UpdateConfig``：把前端完整 ``SettingsConfig`` 拆分——
  * 非密钥字段写入 ``Workspace.settings``（落 SQLite，便于跨会话保留）；
  * 密钥字段（``*Key`` / ``*Secret``）**仅**推入进程内存的
    ``resolve.CONFIG_OVERRIDES`` 覆盖层，绝不落库。

安全模型（三角色 P0）：密钥永不进入 localStorage（前端 partialize 已剥离）
也永不进入 SQLite（本 handler 剥离）。即使两者都失守，密钥也只在进程内存、
随 worker 重启即失效。
"""

from __future__ import annotations

import re
from typing import Any

from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.models import (
    CommandEnvelope,
    CommandResult,
    ConfigSpec,
)
from worker.runtime.providers.resolve import apply_override, read_override

# 合并基线：defaults < env < Workspace.settings < CONFIG_OVERRIDES。
# 注意 env 仅影响真实 provider 解析，设置页展示以 DB + 覆盖层为准，
# 故此处基线不含 env（env 的机密性由 resolve.* 在使用时读取）。
DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "cloud",
        "model": "step-3.7",
        "apiKey": "",
        "baseUrl": "",
        "costPer1k": "0.012",
        "sampling": {"temperature": 0.7, "topP": 0.9, "maxTokens": 2048},
    },
    "asr": {"provider": "cloud", "apiKey": "", "baseUrl": ""},
    "tts": {"provider": "cloud", "apiKey": "", "baseUrl": "", "model": "StepAudio"},
    "workspace": {"defaultPath": ""},
    "brand": {
        "name": "科技实测 · 克制判断",
        "audience": "关注 AI 产品与效率工具的内容用户",
        "tone": "第一人称验证；结论先于功能；避免绝对化判断；"
                "明确个人样本范围；不使用未核验的性能数字。",
        "mustExecute": ["cite-sources", "check-similarity", "human-confirm-risk"],
        "defaultOutput": ["<=90s", "9:16"],
    },
    "data": {
        "retentionDays": 30,
        "desensitize": True,
        "projectDelete": False,
        "uploadScope": "",
    },
    "export": {"format": "MP4", "checkDeps": True},
    "ui": {"theme": "dark", "language": "zh-CN", "logLevel": "info"},
}

# 密钥字段识别：以 Key 结尾（不区分大小写，但排除 passkey）、
# 或含 secret / token / password / passphrase / credential。覆盖非约定名
# 密钥（如 ``llm.token``），避免其明文落 SQLite 或被 GetConfig 回显。
_SECRET_RE = re.compile(r"(?i)(?<!pass)key$|secret$|token$|password$|passphrase$|credential$")


def _deep_merge(base: Any, patch: Any) -> Any:
    """深合并：dict 递归合并；其余（含 list / 标量）整体替换。"""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch if patch is not None else base
    result: dict[str, Any] = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _strip_secrets(value: Any) -> Any:
    """递归剔除所有密钥字段，返回可落库的对象（密钥永不入 SQLite）。"""
    if isinstance(value, dict):
        return {
            k: _strip_secrets(v)
            for k, v in value.items()
            if not _SECRET_RE.search(str(k))
        }
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    return value


def _mask_secrets(value: Any) -> Any:
    """递归把密钥字段值替换为掩码（``"••••"``），用于回显。

    空值（``""`` / ``None``）保持原样——避免把「未配置密钥」
    误显示为「已配置」，误导用户。
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _SECRET_RE.search(str(k)):
                out[k] = "••••" if v else v
            else:
                out[k] = _mask_secrets(v)
        return out
    if isinstance(value, list):
        return [_mask_secrets(v) for v in value]
    return value


def _extract_provider_sections(cfg: dict[str, Any]) -> dict[str, Any]:
    """从完整配置提取 provider 三段（llm / asr / tts）的**完整**配置。

    覆盖层只存内存，故可携带密钥；``resolve.*`` 据此完整重建
    provider，无需回退 env 或读库。DB 落盘仍由 ``_strip_secrets``
    剥离密钥。
    """
    out: dict[str, Any] = {}
    for section in ("llm", "asr", "tts"):
        section_cfg = cfg.get(section)
        if isinstance(section_cfg, dict) and section_cfg:
            out[section] = dict(section_cfg)
    return out


def _derive_resolved(cfg: dict[str, Any]) -> dict[str, Any]:
    """构建解析摘要（供前端「检查配置」展示）。"""
    llm = cfg.get("llm", {}) or {}
    asr = cfg.get("asr", {}) or {}
    tts = cfg.get("tts", {}) or {}
    return {
        "ai": {
            "provider": llm.get("provider"),
            "model": llm.get("model"),
            "hasKey": bool(llm.get("apiKey")),
        },
        "asr": {
            "provider": asr.get("provider"),
            "hasKey": bool(asr.get("apiKey")),
        },
        "tts": {
            "provider": tts.get("provider"),
            "model": tts.get("model"),
            "hasKey": bool(tts.get("apiKey")),
        },
    }


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """处理 GetConfig / UpdateConfig。"""
    repos = deps.repos
    ws = repos.workspaces.ensure(env.workspaceId)

    if env.commandType == "GetConfig":
        # 合并顺序：defaults < 已落库设置 < 内存密钥覆盖层
        merged = _deep_merge(DEFAULT_CONFIG, ws.settings)
        merged = _deep_merge(merged, read_override(env.workspaceId))
        masked = _mask_secrets(merged)
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"config": masked, "resolved": _derive_resolved(merged)},
        )

    if env.commandType == "UpdateConfig":
        try:
            spec = ConfigSpec(**env.payload)
        except Exception as e:  # noqa: BLE001 - 转译为干净的客户端错误
            raise DispatchError("INVALID_ARGUMENT", f"bad config spec: {e}") from None

        incoming = spec.model_dump()
        # 非密钥 → 合并进 DB 设置（保留未提供的字段）
        non_secret = _strip_secrets(incoming)
        new_settings = _deep_merge(ws.settings, non_secret)
        repos.workspaces.update_settings(ws.id, new_settings)
        # 密钥 → 仅推入进程内存覆盖层（携带完整 provider 配置，
        # 含 baseUrl / model / apiKey，供 resolve.* 完整重建）
        secrets = _extract_provider_sections(incoming)
        apply_override(env.workspaceId, secrets)
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"saved": True},
        )

    raise DispatchError(
        "UNKNOWN_COMMAND", f"{env.commandType} not handled by config handler"
    )
