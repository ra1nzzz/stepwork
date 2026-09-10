"""热点打分与排序（**纯函数**：不碰 DB、不碰网络）。

为什么是纯函数：推荐是产品判断，判断错了要能复盘。一旦掺进 IO，就只能
「跑一遍看结果」，没法回答「为什么这条排第一」。纯函数 + 分解出参，用户
看见分数就知道是哪一维拖了后腿。

用户可以要求「换个角度重新推荐」—— 那时**不重新抓网**，只对同一批事实
换权重重算。这也是把 rank 独立出来的直接理由。

四条设计纪律（每条都有对应测试，别退化）：

1. **热度只源内比**。抖音千万级 vs GitHub star 千级 vs 无分值，跨源比 score
   没有意义（上游 ``discover`` 的配额策略也是这个理由）。故 heat 用源内分位。
2. **缺信息取中性值，不当成坏信号**。源没给发布时间 → freshness 取 0.5，
   而不是 0 —— 否则「榜单类源」会被整体压到最低，等于把它们全过滤掉。
3. **品牌契合是闸门，不是加分项**。见 :data:`BRAND_GATE_FLOOR`（这条是
   真机验收打出来的，首版做错）。
4. **惩罚是乘性的**。命中禁用表达不该被「热度高」抵消。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from worker.runtime.hotspot.models import (
    HotspotItem,
    Recommendation,
    ScoreBreakdown,
)
from worker.runtime.script.similarity import jaccard

#: 「内容质量」各维权重（在 core 内部归一化，不必和为 1）。改这里即可调整
#: 推荐取向，不必动算法。
#:
#: **品牌契合不在这里** —— 见 :data:`BRAND_GATE_FLOOR`。
WEIGHTS: dict[str, float] = {
    "freshness": 0.30,
    "heat": 0.20,
    "novelty": 0.10,
    "feedback": 0.10,
}

#: 品牌契合的**闸门下限**：总分 = core × gate × (1 − 惩罚)，其中
#: ``gate = BRAND_GATE_FLOOR + (1 − BRAND_GATE_FLOOR) × brand_fit``。
#:
#: 为什么是闸门而不是 0.30 的加权项（**这是真机验收打出来的结论，别改回去**）：
#: 首版把品牌契合当加权项，验收跑真实热点时「习近平对青岛货轮火灾作出指示」
#: 拿到了 65 分并列第一 —— 因为 brandFit=0 只扣掉 0.30，而时效 1.0 + 热度 1.0
#: + 新颖 1.0 + 反馈 0.5 恰好凑出 0.65，把品牌这一维整个压过去了。
#: 对「AI 工具与效率方法」这种账号，推一条国家灾害新闻是**不可接受**的，
#: 再热也不行 —— 与账号相关是必要条件，不是加分项。
#:
#: 保底 0.20 而非 0：品牌档写得太窄时，硬性归零会让推荐列表直接空掉。
#: 宁可给一个明显落后的低分，也不要给用户一个空列表（空列表看起来像坏了）。
BRAND_GATE_FLOOR = 0.20

#: 时效半衰期（小时）：过了这么久，freshness 衰减到约 0.5
FRESHNESS_HALF_LIFE_HOURS = 24.0

#: 缺信息时的中性值
_NEUTRAL = 0.5

#: 历史反馈 → feedback 分值（0.5 = 无反馈，中性）
_FEEDBACK_SCORES: dict[str, float] = {
    "adopted": 0.20,  # 已经做过了，再推没有增量
    "ignored": 0.35,  # 看过没兴趣
    "rejected": 0.00,  # 明确不合适
}

#: 新颖度低于此值即提示「与历史选题撞车」
_NOVELTY_RISK = 0.30

#: 分词的保留规则（与 similarity 同口径，避免两套归一化各说各话）
_TOKEN_SPLIT = re.compile(r"[^\w一-鿿]+", re.UNICODE)
_CJK = re.compile(r"[一-鿿]")


# ---------------------------------------------------------------------------
# 品牌画像 → 关键词


#: 超过这么长的中文串**不再整体入表**。长句（如定位语「面向普通人的 AI 工具
#: 与效率方法」）整体几乎不可能出现在一条标题里，留在表里只会撑大分母。
#: 取 6 是实测值：内容支柱「效率提升」「自我提升」（4 字）要留住，
#: 而「工具与效率方法」（7 字，明显是跨词切出的噪声）要滤掉。
_MAX_WHOLE_TOKEN = 6


def _cross_script(gram: str) -> bool:
    """2-gram 是否横跨中英边界（如「的a」「i工」）。"""
    return bool(_CJK.search(gram[0])) != bool(_CJK.search(gram[1]))


def _tokens(text: str) -> set[str]:
    """分词：中文按 2-gram，英文/数字按词。

    中文不分词直接取字符集合会丢掉词序信息（「机器学习」和「器习学机」一样），
    故取 2-gram；英文按空白/标点切词即可。

    两条**降噪**规则（真机验收打出来的：不清噪时品牌契合恒为 0.0x）：

    1. 不保留横跨中英边界的 2-gram。中英混排（「AI工具」）会切出「的a」「i工」
       这类纯噪声 —— 它们永远不会出现在标题里，但会把分母撑大、
       把真实命中的比例稀释掉。
    2. 只有较短的串才整体入表。长句整体入表同上，是分母污染。
    """
    norm = (text or "").lower()
    out: set[str] = set()
    for chunk in _TOKEN_SPLIT.split(norm):
        if not chunk:
            continue
        if _CJK.search(chunk):
            if len(chunk) == 1:
                out.add(chunk)
            else:
                # 短串整词与 2-gram **都要**：只留 2-gram 会让「效率提升」这类
                # 四字支柱整词消失，而它恰恰是最强的取向信号；只留整词则匹配
                # 不到「效率提升指南」这类变体。
                if len(chunk) <= _MAX_WHOLE_TOKEN:
                    out.add(chunk)
                out.update(
                    gram
                    for gram in (chunk[i : i + 2] for i in range(len(chunk) - 1))
                    if not _cross_script(gram)
                )
        else:
            out.add(chunk)
    return {t for t in out if len(t) >= 1}


def brand_keywords(brand: dict[str, Any] | None) -> set[str]:
    """从品牌画像抽出关键词集合。

    覆盖：定位 / 受众 / 语气 / 内容支柱 / 六维风格 DNA。风格 DNA 是 JSON，
    值往往是长句，但也照样分词 —— 一句「用反问开场」里的「反问」是实打实
    的取向信号。
    """
    if not brand:
        return set()
    raw_parts: list[str] = []
    for key in ("positioning", "audience", "tone"):
        value = brand.get(key)
        if isinstance(value, str):
            raw_parts.append(value)
    pillars = brand.get("content_pillars") or brand.get("contentPillars")
    if isinstance(pillars, list):
        raw_parts.extend(str(p) for p in pillars)
    dna = brand.get("style_dna") or brand.get("styleDna")
    if isinstance(dna, dict):
        raw_parts.extend(str(v) for v in dna.values() if isinstance(v, str))
    elif isinstance(dna, str):
        raw_parts.append(dna)
    return _tokens(" ".join(raw_parts))


def pillar_keywords(brand: dict[str, Any] | None) -> set[str]:
    """内容支柱的分词。

    单独拎出来是因为**支柱是用户手选的「这个号做什么」**，比定位语里偶然
    切出的一个 2-gram 强得多 —— 打分时给它额外权重（见 :func:`score_brand_fit`）。
    """
    if not brand:
        return set()
    raw = brand.get("content_pillars") or brand.get("contentPillars")
    if not isinstance(raw, list):
        return set()
    return _tokens(" ".join(str(p) for p in raw if str(p).strip()))


def banned_expressions(brand: dict[str, Any] | None) -> list[str]:
    """品牌禁用表达（命中即惩罚）。"""
    if not brand:
        return []
    raw = brand.get("banned_expressions") or brand.get("bannedExpressions")
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


# ---------------------------------------------------------------------------
# 各维打分


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    # SQLite/ISO 常见：末尾 Z 在 3.10 的 fromisoformat 里不认
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def score_freshness(item: HotspotItem, now: datetime | None = None) -> float:
    """时效：指数衰减。**没给时间 = 0.5 中性**，不当成坏信号。"""
    published = _parse_iso(item.published_at)
    if published is None:
        return _NEUTRAL
    base = now or datetime.now(UTC)
    hours = max(0.0, (base - published).total_seconds() / 3600.0)
    # 0.5 ** (t/T) 而非 exp(-t/T)：前者在 t == T 时正好是 0.5，
    # 「半衰期」三个字才名副其实（exp 版本 t=T 时是 0.368）
    return float(0.5 ** (hours / FRESHNESS_HALF_LIFE_HOURS))


def score_heat(item: HotspotItem, *, rank: int, total: int) -> float:
    """热度：**源内分位**，不是原始 score。

    优先用源内名次（上游已按热度排好，且总能拿到）；``total==1`` 时给 0.5
    —— 只有一个元素谈不上分位，给 1.0 会让它凭空超过别源的第二名。
    """
    if total <= 1:
        return _NEUTRAL
    return 1.0 - (rank / (total - 1))


def score_brand_fit(
    item: HotspotItem, keywords: set[str], pillars: set[str] | None = None
) -> float:
    """品牌契合：**品牌词有多少比例出现在热点里**。

    刻意不用 ``similarity.containment``：那是字符 3-gram 口径，而这里两边
    长度极不对等（品牌词十几个 token vs 热点一句话），3-gram 会跨词切出
    大量垃圾片段，实测重叠恒为 0，完全没有区分度。这里直接比 token 集合。

    分母取品牌词数（而非热点 token 数）：我们要问的是「这条热点覆盖了多少
    我的定位」，不是「这条热点有多少字是我的定位」—— 后者会让长文本天然吃亏。

    ``pillars``（内容支柱）命中额外加权：那是用户手选的取向声明，命中一个
    顶三个普通词。
    """
    if not keywords:
        return _NEUTRAL
    text = f"{item.title} {item.summary}".strip()
    if not text:
        return _NEUTRAL
    item_tokens = _tokens(text)
    if not item_tokens:
        return _NEUTRAL
    hit = len(keywords & item_tokens)
    pillar_hit = len((pillars or set()) & item_tokens)
    # 命中 1 个词不该直接满分：×2 后再截断，让「命中多个词」才有区分度
    return float(min(1.0, ((hit + 2 * pillar_hit) / len(keywords)) * 2.0))


def score_novelty(item: HotspotItem, history: list[str]) -> float:
    """新颖度：与历史选题的最大相似度取反。无历史 = 完全新颖。"""
    if not history:
        return 1.0
    text = f"{item.title} {item.summary}".strip()
    if not text:
        return 1.0
    best = max(jaccard(text, h) for h in history)
    return float(max(0.0, 1.0 - best))


def score_feedback(item: HotspotItem, feedback: dict[str, str]) -> float:
    """反馈修正：按 id 命中优先，再按标题命中（跨批次 url 可能变）。"""
    verdict = feedback.get(item.id) or feedback.get(item.title)
    if verdict is None:
        return _NEUTRAL
    return _FEEDBACK_SCORES.get(verdict, _NEUTRAL)


def score_penalty(item: HotspotItem, banned: list[str]) -> tuple[float, list[str]]:
    """硬性惩罚：命中禁用表达 → 1.0（乘性清零）。

    返回 ``(penalty, 命中的词)``：命中的词要出参，用户得知道是**哪个词**
    触发的，否则「你的选题被扣分了」无从改起。
    """
    if not banned:
        return 0.0, []
    text = f"{item.title} {item.summary}"
    hits = [w for w in banned if w and w in text]
    if not hits:
        return 0.0, []
    return 1.0, hits


# ---------------------------------------------------------------------------
# 编排


def _source_totals(items: list[HotspotItem]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for item in items:
        totals[item.source] = totals.get(item.source, 0) + 1
    return totals


def score_item(
    item: HotspotItem,
    *,
    brand: dict[str, Any] | None = None,
    history: list[str] | None = None,
    feedback: dict[str, str] | None = None,
    heat_rank: int = 0,
    heat_total: int = 1,
    now: datetime | None = None,
) -> tuple[ScoreBreakdown, float, list[str]]:
    """给一条热点打分。返回 ``(分解, 总分 0-100, 风险列表)``。"""
    keywords = brand_keywords(brand)
    pillars = pillar_keywords(brand)
    banned = banned_expressions(brand)
    hist = history or []
    fb = feedback or {}

    penalty, banned_hits = score_penalty(item, banned)
    breakdown = ScoreBreakdown(
        freshness=score_freshness(item, now),
        heat=score_heat(item, rank=heat_rank, total=heat_total),
        brand_fit=score_brand_fit(item, keywords, pillars),
        novelty=score_novelty(item, hist),
        feedback=score_feedback(item, fb),
        penalty=penalty,
    )
    weight_sum = sum(WEIGHTS.values()) or 1.0
    core = (
        WEIGHTS["freshness"] * breakdown.freshness
        + WEIGHTS["heat"] * breakdown.heat
        + WEIGHTS["novelty"] * breakdown.novelty
        + WEIGHTS["feedback"] * breakdown.feedback
    ) / weight_sum
    # 品牌契合是闸门：完全无关的内容最高只能拿到 BRAND_GATE_FLOOR 的分数
    gate = BRAND_GATE_FLOOR + (1.0 - BRAND_GATE_FLOOR) * breakdown.brand_fit
    # 惩罚也是乘性的：禁用词不该被「热度高」抵消
    total = max(0.0, core * gate * (1.0 - penalty)) * 100.0

    risks: list[str] = []
    if banned_hits:
        risks.append(f"命中禁用表达：{'、'.join(banned_hits[:3])}")
    if breakdown.novelty < _NOVELTY_RISK:
        risks.append(f"与历史选题高度相似（新颖度 {breakdown.novelty:.0%}）")
    if str(item.meta.get("parse") or "") == "text-fallback":
        risks.append("该条由可见文本兜底解析，字段可能不准")
    return breakdown, total, risks


def rank_items(
    items: list[HotspotItem],
    *,
    brand: dict[str, Any] | None = None,
    history: list[str] | None = None,
    feedback: dict[str, str] | None = None,
    now: datetime | None = None,
) -> list[tuple[HotspotItem, ScoreBreakdown, float, list[str]]]:
    """按总分降序排；**同分稳定排序**（保持上游顺序，避免每次结果跳动）。"""
    totals = _source_totals(items)
    counters: dict[str, int] = {}
    scored: list[tuple[int, HotspotItem, ScoreBreakdown, float, list[str]]] = []
    for index, item in enumerate(items):
        rank = counters.get(item.source, 0)
        counters[item.source] = rank + 1
        breakdown, total, risks = score_item(
            item,
            brand=brand,
            history=history,
            feedback=feedback,
            heat_rank=rank,
            heat_total=totals.get(item.source, 1),
            now=now,
        )
        # 用 (-total, index) 排序：分数相同时保持上游顺序
        scored.append((index, item, breakdown, total, risks))
    scored.sort(key=lambda row: (-row[3], row[0]))
    return [(row[1], row[2], row[3], row[4]) for row in scored]


# ---------------------------------------------------------------------------
# 理由：规则模板（AI 不可用时的降级路径）


def rule_reason(item: HotspotItem, breakdown: ScoreBreakdown, risks: list[str]) -> str:
    """模板理由。**降级路径也要有人话**，不能只丢一个分数给用户。"""
    if risks and risks[0].startswith("命中禁用表达"):
        return f"不推荐：{risks[0]}。"
    bits: list[str] = []
    if breakdown.brand_fit >= 0.6:
        bits.append("与你的内容定位贴合")
    elif breakdown.brand_fit <= 0.2:
        bits.append("与你的内容定位关联较弱")
    if breakdown.freshness >= 0.7:
        bits.append("时效新")
    elif breakdown.freshness <= 0.3:
        bits.append("已有一段时间")
    if breakdown.heat >= 0.7:
        bits.append("在同源里热度靠前")
    if breakdown.novelty < _NOVELTY_RISK:
        bits.append("但与历史选题接近")
    if breakdown.feedback <= 0.2:
        bits.append("此前已表态不感兴趣")
    if not bits:
        bits.append("各项中规中矩")
    return f"{item.title}：" + "，".join(bits) + "。"


def build_recommendation(
    item: HotspotItem,
    breakdown: ScoreBreakdown,
    total: float,
    risks: list[str],
    *,
    reason: str | None = None,
) -> Recommendation:
    """组装推荐项；``reason`` 为空即走规则模板并如实标注来源。"""
    if reason:
        return Recommendation(
            item=item,
            score=total,
            breakdown=breakdown,
            reason=reason,
            reason_source="ai",
            risks=risks,
        )
    return Recommendation(
        item=item,
        score=total,
        breakdown=breakdown,
        reason=rule_reason(item, breakdown, risks),
        reason_source="rule",
        risks=risks,
    )
