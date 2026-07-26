"""A2A 命令（PRD-AGT-005）。

入站（Server）：

- ``StartA2aServer`` / ``StopA2aServer`` / ``GetA2aServerStatus``：显式
  开关远程 Agent 服务。默认不监听 —— 用户不开就没有端口（SYSTEM_SPEC §8.2）。
- ``GetAgentCard``：返回我们自己的 Agent Card，供 UI 展示与调试。

出站（Client）：

- ``AddA2aAgent``：拉取远端 Agent Card 并登记为 AgentConnection +
  AgentCapability（§13.5 的映射）。
- ``CallA2aSkill``：向远端发任务，结果落 AgentTask / AgentArtifact。

与 MCP 客户端共用同一套信任等级与留痕表，UI 的复核入口仍只有一个。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from worker.runtime.agents import a2a_server
from worker.runtime.agents.a2a_card import build_agent_card, parse_remote_card
from worker.runtime.agents.a2a_client import (
    A2aClientError,
    extract_artifact_text,
    fetch_agent_card,
    normalize_base_url,
    send_message,
)
from worker.runtime.commands.bus import DispatchError
from worker.runtime.db.connection import connect
from worker.runtime.db.repos import Repos
from worker.runtime.deps import Deps
from worker.runtime.models import CommandEnvelope, CommandResult

#: 出站 A2A 连接的 protocol 值（与入站 ``a2a`` 分开，同 mcp-client 的理由）
PROTOCOL = "a2a-client"

#: 外部拿回的内容一律未经复核（PRD-AGT-003）
TRUST_LEVEL = "external-unverified"
REVIEW_STATE = "pending_review"

#: 单个结果入库的文本上限
_MAX_RESULT_CHARS = 20000


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _require(payload: dict[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return str(value)
    raise DispatchError("INVALID_ARGUMENT", f"{names[0]} required")


def _load_connection(deps: Deps, conn_id: str) -> dict[str, Any]:
    row = deps.repos.conn.execute(
        "SELECT * FROM agent_connections WHERE id=?", (conn_id,)
    ).fetchone()
    if row is None:
        raise DispatchError("NOT_FOUND", f"connection {conn_id!r} not found")
    record = _row_to_dict(row)
    if record.get("protocol") != PROTOCOL:
        raise DispatchError(
            "INVALID_ARGUMENT",
            f"connection {conn_id!r} 不是出站 A2A 连接"
            f"（protocol={record.get('protocol')!r}）",
        )
    if record.get("status") != "active":
        raise DispatchError(
            "CONNECTION_DISABLED", f"connection {conn_id!r} 已停用，请先在连接页启用"
        )
    return record


def _sync_capabilities(deps: Deps, conn_id: str, skills: list[dict[str, Any]]) -> None:
    """Agent Card 的 skills → agent_capabilities（§13.5 映射；先清后插）。"""
    db = deps.repos.conn
    db.execute("DELETE FROM agent_capabilities WHERE agent_connection_id=?", (conn_id,))
    now = _now()
    for skill in skills:
        db.execute(
            "INSERT INTO agent_capabilities "
            "(id, agent_connection_id, capability_key, capability_schema, enabled, created_at) "
            "VALUES (?,?,?,?,1,?)",
            (
                f"acap_{uuid.uuid4().hex}",
                conn_id,
                skill["id"],
                json.dumps(skill, ensure_ascii=False),
                now,
            ),
        )


def _record_call(
    deps: Deps,
    env: CommandEnvelope,
    *,
    conn_id: str,
    skill_id: str,
    text: str,
    ok: bool,
) -> str:
    """A2A Task → AgentTask，Artifact → AgentArtifact（§13.5）。"""
    db = deps.repos.conn
    now = _now()
    task_id = f"atask_{uuid.uuid4().hex}"
    actor = env.actor or {}
    project_id = str(env.projectId) if env.projectId else None
    db.execute(
        "INSERT INTO agent_tasks "
        "(id, initiator, target_agent_id, session_id, project_id, task_type, "
        "input_artifact_ids, state, progress, cost, timeout_at, "
        "correlation_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            task_id,
            f"{actor.get('type', 'desktop')}:{actor.get('id', 'ui')}",
            conn_id,
            None,
            project_id,
            f"a2a:{skill_id}",
            "[]",
            "succeeded" if ok else "failed",
            1.0 if ok else 0.0,
            None,
            None,
            env.commandId,
            now,
            now,
        ),
    )
    if ok and project_id:
        db.execute(
            "INSERT INTO agent_artifacts "
            "(id, project_id, agent_task_id, artifact_type, schema_version, "
            "producer_agent_id, content_uri_or_json, source_refs, "
            "trust_level, content_hash, review_state, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"aart_{uuid.uuid4().hex}",
                project_id,
                task_id,
                f"a2a:{skill_id}",
                "1",
                conn_id,
                json.dumps({"text": text, "skill": skill_id}, ensure_ascii=False),
                json.dumps([conn_id]),
                TRUST_LEVEL,
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                REVIEW_STATE,
                now,
            ),
        )
    db.commit()
    return task_id


#: 连接 id → 远端 Bearer 令牌。**只在内存**，随 worker 退出即失 ——
#: 与设置页「API Key 绝不落盘」同一条原则，也符合 SYSTEM_SPEC §925
#: 「不在 Agent Card 或配置中嵌入静态敏感凭据」。代价是重启后需重填，
#: 换来的是备份/同步 DB 文件不会连带泄漏对端凭据。
_TOKENS: dict[str, str] = {}


def remember_token(conn_id: str, token: str | None) -> None:
    if token:
        _TOKENS[conn_id] = token
    else:
        _TOKENS.pop(conn_id, None)


def _token_for(conn_id: str) -> str | None:
    return _TOKENS.get(conn_id)


def _db_path(deps: Deps) -> str:
    """当前库的磁盘路径（用于在 Server 线程另开连接）。"""
    row = deps.repos.conn.execute("PRAGMA database_list").fetchone()
    return str(row["file"]) if row and row["file"] else ""


#: Server 线程各自的 Deps（键为线程 id）。ThreadingHTTPServer 每个请求
#: 一个线程，缓存避免每次请求都重开连接。
_THREAD_DEPS: dict[int, Deps] = {}
_THREAD_DEPS_LOCK = threading.Lock()


def _thread_deps(db_path: str, base: Deps) -> Deps:
    """为当前线程取一个可用的 Deps。

    **不能直接复用主线程的连接**：sqlite3 默认 ``check_same_thread=True``，
    跨线程使用直接 ProgrammingError（本模块的测试正是这么撞出来的）。
    也不用 ``check_same_thread=False`` + 全局锁 —— 那会让 A2A 请求和桌面
    端命令互相阻塞。WAL 模式下多连接读写本就安全，各线程各开一条即可。
    """
    key = threading.get_ident()
    with _THREAD_DEPS_LOCK:
        existing = _THREAD_DEPS.get(key)
        if existing is not None:
            return existing
        # **只换 repos**：ai / asr / tts / renderer 等是无状态的 provider
        # 适配器，跨线程共用没问题；线程绑定的只有 SQLite 连接。早先只传
        # repos 的写法会让 A2A 侧所有 AI 命令报「ai provider not configured」。
        deps = replace(base, repos=Repos(connect(db_path)))
        _THREAD_DEPS[key] = deps
        return deps


def reset_thread_deps() -> None:
    """关闭并清空线程级连接缓存（Server 停止时调用，避免句柄泄漏）。"""
    with _THREAD_DEPS_LOCK:
        for deps in _THREAD_DEPS.values():
            try:
                deps.repos.conn.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响停机
                pass
        _THREAD_DEPS.clear()


def _make_executor(deps: Deps, env: CommandEnvelope) -> Any:
    """给 A2A Server 用的执行器：Skill → Command Bus。

    信封写死 ``source="a2a"`` / ``actor.type="agent"``，于是默认拒绝清单
    与 §9.1 审批降级自动生效 —— A2A 这一层不自建权限。
    """
    # 延迟导入：bus 会 import handlers，模块级导入会成环
    from worker.runtime.commands.bus import dispatch

    db_path = _db_path(deps)
    workspace_id = env.workspaceId

    def _execute(command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        raw = {
            "commandId": uuid.uuid4().hex,
            "commandType": command_type,
            "schemaVersion": "1",
            "actor": {"type": "agent", "id": "a2a-remote"},
            "source": "a2a",
            "workspaceId": workspace_id,
            "requestedAt": _now(),
            "payload": payload,
        }
        # 内存库没有磁盘路径，无法跨线程另开；此时只能沿用原 Deps
        # （测试用内存库时会退回旧行为，生产一律走文件库）
        run_deps = _thread_deps(db_path, deps) if db_path else deps
        # Server 跑在自己的线程里，没有事件循环，故起一个临时循环
        return asyncio.run(dispatch(raw, run_deps))

    return _execute


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    payload = env.payload or {}

    # ---- 入站 Server ----

    if env.commandType == "GetAgentCard":
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"card": build_agent_card(a2a_server.STATE.base_url or "http://127.0.0.1")},
        )

    if env.commandType == "StartA2aServer":
        port = payload.get("port")
        state = a2a_server.start(_make_executor(deps, env), int(port or 0))
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "running": True,
                "url": state.base_url,
                "card_url": f"{state.base_url}/.well-known/agent.json",
                # 令牌只在启动时回给发起方（桌面 UI），不写库、不进日志
                "token": state.token,
            },
        )

    if env.commandType == "StopA2aServer":
        stopped = await a2a_server.stop_async()
        reset_thread_deps()
        return CommandResult(
            ok=True, commandId=env.commandId, detail={"stopped": stopped, "running": False}
        )

    if env.commandType == "GetA2aServerStatus":
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={"running": a2a_server.STATE.running, "url": a2a_server.STATE.base_url},
        )

    # ---- 出站 Client ----

    if env.commandType == "AddA2aAgent":
        url = _require(payload, "url")
        token = str(payload.get("token") or "") or None
        try:
            base = normalize_base_url(url)
            card = await fetch_agent_card(base, token=token)
        except A2aClientError as e:
            raise DispatchError(e.code, e.message) from e
        name, skills = parse_remote_card(card)

        conn_id = f"a2ac_{uuid.uuid4().hex[:12]}"
        now = _now()
        deps.repos.conn.execute(
            "INSERT INTO agent_connections "
            "(id, protocol, endpoint_or_command, local_or_remote, trust_level, "
            "auth_ref, status, capabilities, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                conn_id,
                PROTOCOL,
                base,
                "remote",
                TRUST_LEVEL,
                None,
                "active",
                json.dumps(skills, ensure_ascii=False),
                now,
                now,
            ),
        )
        _sync_capabilities(deps, conn_id, skills)
        deps.repos.conn.commit()
        remember_token(conn_id, token)
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "connection_id": conn_id,
                "agent_name": name,
                "url": base,
                "skills": skills,
                # 明确告知令牌未落盘，重启后要重填（避免用户以为已保存）
                "token_persisted": False,
            },
        )

    if env.commandType == "CallA2aSkill":
        conn_id = _require(payload, "connectionId", "connection_id")
        skill_id = _require(payload, "skillId", "skill_id")
        text = str(payload.get("text") or "")
        record = _load_connection(deps, conn_id)
        # 本次调用可临时带令牌（重启后重填的入口），带了就记住
        override = str(payload.get("token") or "") or None
        if override:
            remember_token(conn_id, override)
        try:
            result = await send_message(
                str(record["endpoint_or_command"]),
                text,
                skill_id=skill_id,
                token=_token_for(conn_id),
            )
        except A2aClientError as e:
            _record_call(
                deps, env, conn_id=conn_id, skill_id=skill_id, text="", ok=False
            )
            raise DispatchError(e.code, e.message) from e

        out = extract_artifact_text(result)[:_MAX_RESULT_CHARS]
        task_id = _record_call(
            deps, env, conn_id=conn_id, skill_id=skill_id, text=out, ok=True
        )
        status = result.get("status")
        return CommandResult(
            ok=True,
            commandId=env.commandId,
            detail={
                "agent_task_id": task_id,
                "skill": skill_id,
                "text": out,
                "remote_state": (status or {}).get("state") if isinstance(status, dict) else None,
                "trust_level": TRUST_LEVEL,
                "review_state": REVIEW_STATE,
            },
        )

    raise DispatchError(
        "UNKNOWN_COMMAND",
        f"commandType {env.commandType!r} not handled by a2a handler",
    )
