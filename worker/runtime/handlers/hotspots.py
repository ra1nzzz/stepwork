"""热点命令：发现 → 推荐 → 反馈 → 转选题（S5，migrations/0014）。

五个命令：

- ``ListHotspotSources``：列出上游可用源（含是否需登录）—— 让 UI 能把
  「热点宝要登录」这件事说在前面，而不是抓完才报错。
- ``DiscoverHotspots``：调上游 MCP 抓热点并落 ``hotspot_items``。
- ``RecommendHotspots``：**推荐**，不是列清单。打分（规则）+ 理由（AI，
  不可用时降级规则）＋ 风险标记。
- ``RecordHotspotFeedback``：采纳/忽略/拒绝，喂给下一轮推荐。
- ``ConvertHotspotToTopic``：把一条热点装订成「选题简报」内容版本，交给
  既有的 ``GenerateTopic`` 消费。

为什么发现和推荐分开：发现是取事实（换源不影响下游），推荐是下判断（吃
品牌画像 + 历史选题 + 用户反馈）。合成一个命令，就没法「换个角度重新推荐」
而不重新抓一遍全网。

为什么转换是**独立一步**而不是塞进 ``GenerateTopic``：热点是外部未核实内容
（PRD-AGT-003），不该直接当选题产出。中间隔一份带出处与信任等级的简报，
人/Agent 过一次目，也留下「这批选题是从哪条热点来的」的审计链。转换本身
**不调 AI**（简报由事实拼装），因此不建 job —— 与 ``ImportSource`` 的本地
文件路径同理。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from worker.runtime.agents.channel import REVIEW_STATE, TRUST_LEVEL
from worker.runtime.commands.bus import DispatchError
from worker.runtime.deps import Deps
from worker.runtime.handlers.brand import (
    format_brand_prompt_block,
    load_project_brand,
)
from worker.runtime.hotspot import brief as hotspot_brief
from worker.runtime.hotspot import mcp as hotspot_mcp
from worker.runtime.hotspot.mcp import SERVER_MARKER
from worker.runtime.hotspot.models import VERDICTS, HotspotItem
from worker.runtime.hotspot.rank import (
    build_recommendation,
    rank_items,
)
from worker.runtime.models import CommandEnvelope, CommandResult, ContentVersion
from worker.runtime.providers.resolve import ai_provider_from_hint
from worker.runtime.script.history import load_topic_history

#: 一次最多让 AI 写几条理由。理由写多了既贵又没人看 —— 用户只需要看 topN
#: 为什么是它们，剩下的有分数和分解就够。
_DEFAULT_REASON_TOP_N = 5
_MAX_REASON_TOP_N = 20

#: AI 写理由的返回契约
REASON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reasons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["reasons"],
    "additionalProperties": False,
}


def _int(payload: dict[str, Any], key: str, default: int, *, lo: int, hi: int) -> int:
    raw = payload.get(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise DispatchError("INVALID_ARGUMENT", f"{key} must be an integer") from None
    return max(lo, min(hi, value))


def _str_list(payload: dict[str, Any], key: str) -> list[str] | None:
    raw = payload.get(key)
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise DispatchError("INVALID_ARGUMENT", f"{key} must be an array of strings")
    return [str(x) for x in raw]


# ---------------------------------------------------------------------------
# ListHotspotSources


async def _list_sources(env: CommandEnvelope, deps: Deps) -> CommandResult:
    payload = env.payload or {}
    data, conn_id = await hotspot_mcp.list_sources(
        deps, connection_id=payload.get("connectionId")
    )
    sources = data.get("sources") if isinstance(data, dict) else None
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        # detail 键名与 results/models.py 的契约一致（snake_case）：
        # 该契约是 pydantic 模型，extra="forbid"，写 camelCase 会直接报错
        detail={
            "connection_id": conn_id,
            "sources": sources if isinstance(sources, list) else [],
            "server_marker": SERVER_MARKER,
        },
    )


# ---------------------------------------------------------------------------
# DiscoverHotspots


async def _discover(env: CommandEnvelope, deps: Deps) -> CommandResult:
    payload = env.payload or {}
    repos = deps.repos
    repos.workspaces.ensure(env.workspaceId)

    sources = _str_list(payload, "sources")
    limit = _int(payload, "limit", 30, lo=1, hi=100)
    window_hours = _int(payload, "windowHours", 48, lo=1, hi=24 * 30)
    query = payload.get("query")
    if query is not None and not isinstance(query, str):
        raise DispatchError("INVALID_ARGUMENT", "query must be a string")
    save = bool(payload.get("save", True))

    data, conn_id = await hotspot_mcp.discover(
        deps,
        sources=sources,
        limit=limit,
        window_hours=window_hours,
        query=query,
        connection_id=payload.get("connectionId"),
    )

    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        raise DispatchError("UPSTREAM_ERROR", "热点 Server 未返回 items 数组")

    batch_id = f"hsb_{uuid.uuid4().hex[:12]}"
    now = _now_iso()
    items: list[HotspotItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        items.append(HotspotItem.from_mcp(raw, batch_id=batch_id, discovered_at=now))

    saved = repos.hotspots.insert_many(env.workspaceId, items) if save and items else 0

    errors = data.get("errors") if isinstance(data.get("errors"), list) else []
    skipped = data.get("skipped") if isinstance(data.get("skipped"), list) else []
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        detail={
            "batch_id": batch_id,
            "connection_id": conn_id,
            "items": [it.to_dict() for it in items],
            "count": len(items),
            "saved": saved,
            "sources": [str(s) for s in (data.get("sources") or [])],
            "errors": errors,
            "skipped": skipped,
        },
    )


# ---------------------------------------------------------------------------
# RecommendHotspots


async def _recommend(env: CommandEnvelope, deps: Deps) -> CommandResult:
    payload = env.payload or {}
    repos = deps.repos
    repos.workspaces.ensure(env.workspaceId)

    limit = _int(payload, "limit", 10, lo=1, hi=50)
    reason_top_n = _int(
        payload, "reasonTopN", _DEFAULT_REASON_TOP_N, lo=0, hi=_MAX_REASON_TOP_N
    )
    sources = _str_list(payload, "sources")

    # 候选来源：默认最近一批；给了 sources 就跨批次取（按源过滤）
    batch_id = payload.get("batchId")
    if isinstance(batch_id, str) and batch_id:
        items = repos.hotspots.list_by_batch(batch_id)
    elif sources:
        items = repos.hotspots.list_recent(env.workspaceId, limit=200, sources=sources)
    else:
        latest = repos.hotspots.latest_batch_id(env.workspaceId)
        items = repos.hotspots.list_by_batch(latest) if latest else []
    if not items:
        raise DispatchError(
            "NOT_FOUND", "没有可推荐的热点：请先运行 DiscoverHotspots"
        )

    project_id = env.projectId or repos.projects.get_or_create_default(
        env.workspaceId
    ).id
    use_brand = bool(payload.get("useBrandProfile", True))
    brand = load_project_brand(repos, project_id) if use_brand else None

    # 历史选题（新颖度）：复用 GenerateTopic 的同一套，避免两套「重复」口径
    history_rows = load_topic_history(repos.conn, project_id)
    history = [str(h.get("text") or "") for h in history_rows if h.get("text")]

    # 反馈：id 命中优先，标题兜底（跨批次 url 变了 id 也会变）
    feedback = dict(repos.hotspots.feedback_map(env.workspaceId))
    for title, verdict in repos.hotspots.title_feedback_map(env.workspaceId).items():
        feedback.setdefault(title, verdict)

    ranked = rank_items(items, brand=brand, history=history, feedback=feedback)
    ranked = ranked[:limit]

    reasons, reason_source, reason_note = await _reasons(
        deps, payload, [row[0] for row in ranked[:reason_top_n]], brand
    )

    recommendations = [
        build_recommendation(item, breakdown, total, risks, reason=reasons.get(item.id))
        for item, breakdown, total, risks in ranked
    ]
    detail: dict[str, Any] = {
        "recommendations": [r.to_dict() for r in recommendations],
        "count": len(recommendations),
        "considered": len(items),
        "reason_source": reason_source,
        "brand_applied": brand is not None,
    }
    if reason_note:
        detail["reason_note"] = reason_note
    return CommandResult(ok=True, commandId=env.commandId, detail=detail)


async def _reasons(
    deps: Deps,
    payload: dict[str, Any],
    items: list[HotspotItem],
    brand: dict[str, Any] | None,
) -> tuple[dict[str, str], str, str]:
    """给 topN 条写「为什么推荐」。

    返回 ``(理由映射, 来源标记, 降级说明)``。**AI 不可用不是错误**：理由降级
    成规则模板，推荐照样能用，但 ``reasonSource`` 会如实写成 ``rule``，
    不让 UI 把模板句当 AI 洞见展示。
    """
    if not items:
        return {}, "rule", ""
    ai = ai_provider_from_hint(payload.get("provider")) or deps.ai
    if ai is None:
        return {}, "rule", "未配置 AI Provider，理由由规则模板生成"
    prompt = _build_reason_prompt(items, brand)
    try:
        raw = await ai.complete(prompt, REASON_SCHEMA)
    except Exception as e:  # noqa: BLE001 - 理由失败不该让推荐失败
        return {}, "rule", f"AI 写理由失败（{type(e).__name__}），已降级为规则模板"
    parsed = _parse_reasons(raw)
    if not parsed:
        return {}, "rule", "AI 未返回可用理由，已降级为规则模板"
    return parsed, "ai", ""


def _build_reason_prompt(items: list[HotspotItem], brand: dict[str, Any] | None) -> str:
    lines: list[str] = []
    if brand:
        lines.append(format_brand_prompt_block(brand))
        lines.append("")
    lines.append(
        "下面是刚抓到的一批热点。请为每一条写**一句**「为什么值得这个账号做」，"
        "20-40 字，说人话，不要复述标题，不要空泛夸赞（如「很有价值」）。"
    )
    lines.append("")
    for item in items:
        bits = [f"- id={item.id} 标题：{item.title}"]
        if item.summary:
            bits.append(f"  摘要：{item.summary[:120]}")
        if item.source:
            bits.append(f"  来源：{item.source}")
        lines.extend(bits)
    lines.append("")
    lines.append("按 JSON 契约返回 reasons 数组，每条含 id 与 reason。")
    return "\n".join(lines)


def _parse_reasons(raw: Any) -> dict[str, str]:
    """从 AI 回包取 ``{id: reason}``；形状不对照样返回空（调用方降级）。"""
    data = raw
    if isinstance(raw, (str, bytes)):
        import json  # noqa: PLC0415

        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(data, dict):
        return {}
    rows = data.get("reasons")
    if not isinstance(rows, list):
        return {}
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").strip()
        reason = str(row.get("reason") or "").strip()
        if rid and reason:
            out[rid] = reason
    return out


# ---------------------------------------------------------------------------
# RecordHotspotFeedback


async def _feedback(env: CommandEnvelope, deps: Deps) -> CommandResult:
    payload = env.payload or {}
    repos = deps.repos
    repos.workspaces.ensure(env.workspaceId)

    hotspot_id = payload.get("hotspotId")
    if not isinstance(hotspot_id, str) or not hotspot_id.strip():
        raise DispatchError("INVALID_ARGUMENT", "hotspotId required")
    verdict = str(payload.get("verdict") or "").strip()
    if verdict not in VERDICTS:
        raise DispatchError(
            "INVALID_ARGUMENT", f"verdict must be one of {', '.join(VERDICTS)}"
        )
    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise DispatchError("INVALID_ARGUMENT", "reason must be a string")
    project_id = env.projectId or payload.get("projectId")

    repos.hotspots.record_feedback(
        hotspot_id=hotspot_id.strip(),
        workspace_id=env.workspaceId,
        verdict=verdict,
        project_id=str(project_id) if project_id else None,
        reason=reason or None,
    )
    return CommandResult(
        ok=True,
        commandId=env.commandId,
        detail={"hotspot_id": hotspot_id.strip(), "verdict": verdict},
    )


# ---------------------------------------------------------------------------
# ConvertHotspotToTopic


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    raw = payload.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise DispatchError("INVALID_ARGUMENT", f"{key} must be a string")
    return raw or None


async def _convert(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """把一条热点转成「选题简报」内容版本（方案 A）。

    产出**不是**选题本身，而是一份带出处 + 信任等级 + （可选）推荐理由与打分
    分解的素材包。下一步由调用方拿它的 id 去跑既有的 ``GenerateTopic``：

    ``GenerateTopic(sourceVersionId=<本命令返回的 content_version_id>)``

    这样「热点 → 选题」的语义边界落在两处既有一致的地方：外部内容必须先声明
    未核实（本命令的 producer），判断必须由 AI 生成角度（GenerateTopic）。
    """
    payload = env.payload or {}
    repos = deps.repos
    repos.workspaces.ensure(env.workspaceId)

    hotspot_id = payload.get("hotspotId")
    if not isinstance(hotspot_id, str) or not hotspot_id.strip():
        raise DispatchError("INVALID_ARGUMENT", "hotspotId required")
    hotspot_id = hotspot_id.strip()

    reason = _optional_str(payload, "reason")
    reason_source = _optional_str(payload, "reasonSource")
    breakdown = payload.get("breakdown")
    if breakdown is not None and not isinstance(breakdown, dict):
        raise DispatchError(
            "INVALID_ARGUMENT", "breakdown must be an object (RecommendHotspots 的 breakdown)"
        )

    # 限定本工作区：推荐页给的 id 全局唯一，但「别人工作区抓到的热点」不该
    # 能借 id 转到本项目来
    item = repos.hotspots.get(hotspot_id, env.workspaceId)
    if item is None:
        raise DispatchError(
            "NOT_FOUND",
            f"热点 {hotspot_id} 不在本工作区：请先运行 DiscoverHotspots 落库",
        )

    project_id = env.projectId or payload.get("projectId")
    if not project_id:
        project_id = repos.projects.get_or_create_default(env.workspaceId).id
    project_id = str(project_id)

    text = hotspot_brief.build_brief(
        item,
        reason=reason,
        reason_source=reason_source,
        breakdown=breakdown if isinstance(breakdown, dict) else None,
    )
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    # 同一（热点, 理由, 分解）→ 同一份简报：Agent 重试不该刷出版本洪水
    existing = repos.content_versions.find_by_hash(
        project_id, hotspot_brief.HOTSPOT_BRIEF_CONTENT_TYPE, content_hash
    )
    reused = existing is not None
    cv_id = existing
    if cv_id is None:
        cv = ContentVersion(
            project_id=project_id,
            # 没有父版本：它的来源不是本系统里的某个版本，而是外部热点
            parent_version_id=None,
            content_type=hotspot_brief.HOTSPOT_BRIEF_CONTENT_TYPE,
            content=text,
            content_hash=content_hash,
            producer=hotspot_brief.brief_producer(
                item,
                reason_source=reason_source,
                breakdown=breakdown if isinstance(breakdown, dict) else None,
            ),
        )
        cv_id = repos.content_versions.insert(cv)

    return CommandResult(
        ok=True,
        commandId=env.commandId,
        artifact_ids=[cv_id],
        detail={
            "content_version_id": cv_id,
            "hotspot_id": item.id,
            "source": item.source,
            "title": item.title,
            "url": item.url,
            # PRD-AGT-003：外部内容必须自报家门的两个字段
            "trust_level": TRUST_LEVEL,
            "review_state": REVIEW_STATE,
            # 理由与分解是「有没有传」的如实记录，不是转换命令猜出来的
            "reason_source": reason_source or "none",
            "breakdown_attached": isinstance(breakdown, dict) and bool(breakdown),
            "reused": reused,
            # 供前端/Agent 直接渲染，无需再查一次 content-fetch
            "brief": text,
            # 下一步：把简报当源跑既有命令，热点不必再走一条新链路。
            # ⚠️ 这里用 snake_case 不是为了好看 —— ``GenerateTopic`` 的
            # ``TopicProposalSpec`` 直接吃 payload，没有 camelCase 别名，
            # 写成 sourceVersionId 会当场 INVALID_ARGUMENT。本域其余命令用的是
            # camelCase（hotspotId / reasonTopN），两套约定并存是既成事实，
            # 照抄这一行才是对的。
            "next_step": {
                "command": "GenerateTopic",
                "payload": {"source_version_id": cv_id},
            },
        },
    )


# ---------------------------------------------------------------------------


def _now_iso() -> str:
    from datetime import UTC, datetime  # noqa: PLC0415

    return datetime.now(UTC).isoformat()


async def handle(env: CommandEnvelope, deps: Deps) -> CommandResult:
    """按 ``commandType`` 分派本模块的五个命令。"""
    if env.commandType == "ListHotspotSources":
        return await _list_sources(env, deps)
    if env.commandType == "DiscoverHotspots":
        return await _discover(env, deps)
    if env.commandType == "RecommendHotspots":
        return await _recommend(env, deps)
    if env.commandType == "RecordHotspotFeedback":
        return await _feedback(env, deps)
    if env.commandType == "ConvertHotspotToTopic":
        return await _convert(env, deps)
    raise DispatchError("INVALID_ARGUMENT", f"unsupported command: {env.commandType}")
