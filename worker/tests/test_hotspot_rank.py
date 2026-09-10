"""热点打分与排序（worker/runtime/hotspot/rank.py）。

rank 是「产品判断」的落点，判断错了要能复盘。故覆盖的重点不是「能算出来」，
而是**每一维的语义是否符合设计意图** —— 尤其是三条纪律：热度只源内比、
缺信息取中性、惩罚是乘性的。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from worker.runtime.hotspot.models import HotspotItem
from worker.runtime.hotspot.rank import (
    BRAND_GATE_FLOOR,
    WEIGHTS,
    _tokens,
    banned_expressions,
    brand_keywords,
    build_recommendation,
    pillar_keywords,
    rank_items,
    rule_reason,
    score_brand_fit,
    score_feedback,
    score_freshness,
    score_heat,
    score_novelty,
    score_penalty,
)

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


def item(
    title: str,
    *,
    source: str = "toutiao_hot",
    summary: str = "",
    published_at: str | None = None,
    score: float | None = None,
    meta: dict[str, Any] | None = None,
    item_id: str | None = None,
) -> HotspotItem:
    return HotspotItem(
        id=item_id or f"id_{title}",
        source=source,
        title=title,
        summary=summary,
        published_at=published_at,
        score=score,
        meta=meta or {},
    )


def iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


# ---------------------------------------------------------------------------
# 品牌画像 → 关键词


def test_brand_keywords_covers_all_fields() -> None:
    brand = {
        "positioning": "面向创业者的 AI 工具评测",
        "audience": "中小商家",
        "tone": "犀利",
        "contentPillars": ["效率提升", "避坑指南"],
        "styleDna": {"hookDna": "用反问开场"},
    }
    keys = brand_keywords(brand)
    assert "创业" in keys  # 中文 2-gram
    assert "中小商家" in keys or "中小" in keys
    assert "效率提升" in keys
    assert "反问" in keys


def test_brand_keywords_tolerates_snake_case() -> None:
    # 画像可能来自 DB 行（snake_case）而非命令出参（camelCase）
    assert brand_keywords({"content_pillars": ["直播带货"]}) & {"直播", "直播带货"}


def test_brand_keywords_empty_when_no_brand() -> None:
    assert brand_keywords(None) == set()
    assert brand_keywords({}) == set()


def test_banned_expressions_accepts_both_shapes() -> None:
    assert banned_expressions({"bannedExpressions": ["家人们", "  "]}) == ["家人们"]
    assert banned_expressions({"banned_expressions": ["冲鸭"]}) == ["冲鸭"]
    assert banned_expressions(None) == []


# ---------------------------------------------------------------------------
# 各维


def test_freshness_decays_and_is_neutral_without_time() -> None:
    assert score_freshness(item("a", published_at=iso(0)), NOW) == pytest.approx(1.0)
    assert score_freshness(item("a", published_at=iso(24)), NOW) == pytest.approx(0.5, abs=0.02)
    # 源没给时间 → 中性，不当成坏信号（否则榜单类源会被整体压死）
    assert score_freshness(item("a"), NOW) == 0.5


def test_freshness_never_negative_for_future() -> None:
    future = (NOW + timedelta(hours=5)).isoformat()
    assert score_freshness(item("a", published_at=future), NOW) == pytest.approx(1.0)


def test_heat_is_within_source_quantile_not_absolute() -> None:
    # 源内第 1 名 = 1.0，最后一名 = 0.0；与 score 绝对值无关
    assert score_heat(item("a", score=9_999_999), rank=0, total=3) == 1.0
    assert score_heat(item("b", score=100), rank=2, total=3) == 0.0
    # 只有一个元素谈不上分位 → 中性
    assert score_heat(item("c"), rank=0, total=1) == 0.5


def test_brand_fit_neutral_without_keywords() -> None:
    assert score_brand_fit(item("任意标题"), set()) == 0.5


def test_brand_fit_rewards_overlap() -> None:
    keys = brand_keywords({"positioning": "AI 工具评测", "contentPillars": ["效率提升"]})
    hit = score_brand_fit(item("这款 AI 工具让效率提升一倍"), keys)
    miss = score_brand_fit(item("今天天气不错大家出去玩"), keys)
    assert hit > miss
    assert hit > 0.5 > miss


def test_novelty_is_one_without_history() -> None:
    assert score_novelty(item("任意"), []) == 1.0


def test_novelty_drops_for_repeated_topic() -> None:
    history = ["秋天穿搭的五个小技巧"]
    fresh = score_novelty(item("冬天保暖装备清单"), history)
    repeat = score_novelty(item("秋天穿搭的五个小技巧"), history)
    assert repeat == pytest.approx(0.0, abs=0.01)
    assert fresh > 0.9


def test_feedback_scores() -> None:
    assert score_feedback(item("a"), {}) == 0.5
    assert score_feedback(item("a"), {"id_a": "rejected"}) == 0.0
    assert score_feedback(item("a"), {"id_a": "adopted"}) == 0.20
    # 跨批次 id 变了时按标题命中
    assert score_feedback(item("a"), {"a": "rejected"}) == 0.0


def test_penalty_reports_hit_words() -> None:
    penalty, hits = score_penalty(item("家人们冲鸭"), ["家人们", "冲鸭"])
    assert penalty == 1.0
    assert set(hits) == {"家人们", "冲鸭"}
    assert score_penalty(item("正常标题"), ["家人们"]) == (0.0, [])


# ---------------------------------------------------------------------------
# 总分与排序


def test_banned_expression_zeroes_total() -> None:
    brand = {"bannedExpressions": ["家人们"]}
    bad = rank_items([item("家人们这款真香")], brand=brand, now=NOW)[0]
    good = rank_items([item("这款工具真香")], brand=brand, now=NOW)[0]
    assert bad[2] == 0.0
    assert good[2] > 0
    assert any("禁用表达" in r for r in bad[3])


def test_penalty_is_multiplicative_not_additive() -> None:
    """禁用词不该被「热度高」抵消 —— 这是乘性惩罚的唯一理由。"""
    brand = {"bannedExpressions": ["家人们"]}
    # 让「坏」那条在其它维度上尽可能满分：最新、源内第一
    rows = rank_items(
        [
            item("家人们这款真香", published_at=iso(0), score=1_000_000),
            item("另一条普通热点", published_at=iso(0)),
        ],
        brand=brand,
        now=NOW,
    )
    bad = next(r for r in rows if "家人们" in r[0].title)
    good = next(r for r in rows if "家人们" not in r[0].title)
    assert bad[2] == 0.0
    assert good[2] > 0
    assert bad[0].score == 1_000_000  # 热度确实高，但没用


def test_ranking_is_stable_for_equal_scores() -> None:
    # 两条都无时间、无品牌、无历史 → 分数完全相同，应保持上游顺序
    rows = rank_items([item("第一条"), item("第二条"), item("第三条")], now=NOW)
    assert [r[0].title for r in rows] == ["第一条", "第二条", "第三条"]


def test_heat_rank_uses_upstream_order_within_source() -> None:
    # 上游已按热度排好：第一条 heat=1.0，第三条 heat=0.0
    rows = rank_items(
        [item("a"), item("b"), item("c")],
        brand=None,
        history=None,
        now=NOW,
    )
    by_title = {r[0].title: r[1].heat for r in rows}
    assert by_title["a"] == 1.0
    assert by_title["b"] == 0.5
    assert by_title["c"] == 0.0


def test_cross_source_heat_does_not_leak() -> None:
    """GitHub star 千级不该输给抖音千万级 —— 各源独立算分位。"""
    rows = rank_items(
        [
            item("抖音第一", source="douyin_hot", score=10_000_000),
            item("抖音第二", source="douyin_hot", score=9_000_000),
            item("GitHub 第一", source="github_trending", score=4_000),
            item("GitHub 第二", source="github_trending", score=3_000),
        ],
        now=NOW,
    )
    heat = {(r[0].source, r[0].title): r[1].heat for r in rows}
    assert heat[("github_trending", "GitHub 第一")] == 1.0
    assert heat[("douyin_hot", "抖音第二")] == 0.0


def test_core_weights_are_positive_and_normalised_at_use() -> None:
    # core 权重在算分时归一化（不必和为 1），但不能为负 —— 负权重会让
    # 「时效越旧分越高」这种反直觉排序悄悄生效
    assert all(w > 0 for w in WEIGHTS.values())
    assert "brand_fit" not in WEIGHTS, "品牌契合是闸门，不是加权项"


def test_irrelevant_hotspot_cannot_outrank_a_relevant_one() -> None:
    """真机验收打出来的回归（首版做错了，别改回去）。

    场景：一个「AI 工具与效率方法」账号。一条与它毫不相干但「最新最热」的
    社会新闻（brandFit=0.00），对上一条品牌契合但稍旧的热点。

    首版把品牌契合当 0.30 的加权项 → 社会新闻靠时效 1.0 + 热度 1.0 + 新颖 1.0
    + 反馈 0.5 凑到 0.65，**并列第一**。这是不可接受的：再热也不能推一条与
    账号无关的灾害新闻。
    """
    brand = {
        "positioning": "面向普通人的 AI 工具与效率方法",
        "contentPillars": ["AI 工具", "效率提升"],
    }
    off_topic = item("青岛货轮火灾25人遇难", published_at=iso(0), score=9_000_000)
    on_brand = item("这款 AI 工具让效率提升一倍", published_at=iso(6), score=1_000)

    rows = rank_items([off_topic, on_brand], brand=brand, now=NOW)
    by_title = {r[0].title: r[2] for r in rows}
    assert by_title["这款 AI 工具让效率提升一倍"] > by_title["青岛货轮火灾25人遇难"]
    # 无关内容的分数必须被闸门明显压低，而不是「仅仅低一点」
    assert by_title["青岛货轮火灾25人遇难"] <= 100 * BRAND_GATE_FLOOR + 0.01


def test_brand_gate_floor_keeps_irrelevant_items_visible() -> None:
    """闸门保底不为 0：品牌档写得太窄时不能把列表直接清空。"""
    brand = {"contentPillars": ["一个几乎不可能出现的词"]}
    rows = rank_items([item("任意热点", published_at=iso(0))], brand=brand, now=NOW)
    assert rows[0][2] > 0.0
    assert BRAND_GATE_FLOOR > 0.0


def test_text_fallback_meta_is_a_risk() -> None:
    rows = rank_items([item("a", meta={"parse": "text-fallback"})], now=NOW)
    assert any("兜底" in r for r in rows[0][3])


def test_novelty_risk_when_close_to_history() -> None:
    rows = rank_items([item("秋天穿搭技巧")], history=["秋天穿搭技巧"], now=NOW)
    assert any("历史选题" in r for r in rows[0][3])


# ---------------------------------------------------------------------------
# 理由


def test_rule_reason_mentions_banned_hit() -> None:
    rows = rank_items([item("家人们冲")], brand={"bannedExpressions": ["家人们"]}, now=NOW)
    i, b, total, risks = rows[0]
    assert "不推荐" in rule_reason(i, b, risks)


def test_rule_reason_describes_strengths() -> None:
    keys = brand_keywords({"positioning": "AI 工具评测"})
    i = item("这款 AI 工具", published_at=iso(0), summary="AI 工具评测")
    b_fit = score_brand_fit(i, keys)
    from worker.runtime.hotspot.models import ScoreBreakdown

    breakdown = ScoreBreakdown(
        freshness=1.0, heat=1.0, brand_fit=b_fit, novelty=1.0, feedback=0.5, penalty=0.0
    )
    text = rule_reason(i, breakdown, [])
    assert "定位" in text or "时效" in text or "热度" in text


def test_build_recommendation_marks_reason_source() -> None:
    from worker.runtime.hotspot.models import ScoreBreakdown

    b = ScoreBreakdown(0.5, 0.5, 0.5, 0.5, 0.5, 0.0)
    i = item("x")
    assert build_recommendation(i, b, 50.0, [], reason="AI 写的").reason_source == "ai"
    assert build_recommendation(i, b, 50.0, []).reason_source == "rule"
    # 降级也要有人话，不能只丢一个分数
    assert build_recommendation(i, b, 50.0, []).reason


def test_recommendation_to_dict_shape() -> None:
    from worker.runtime.hotspot.models import ScoreBreakdown

    b = ScoreBreakdown(0.5, 0.5, 0.5, 0.5, 0.5, 0.0)
    rec = build_recommendation(item("标题"), b, 62.345, ["风险"], reason="理由")
    out = rec.to_dict()
    assert out["title"] == "标题"
    assert out["score"] == pytest.approx(62.35, abs=0.01)
    assert out["reasonSource"] == "ai"
    assert out["risks"] == ["风险"]
    assert {"freshness", "heat", "brandFit", "novelty", "feedback", "penalty"} == set(
        out["breakdown"]
    )


def test_tokens_keeps_whole_word_and_bigrams() -> None:
    # 整词与 2-gram 并存：才有「效率提升」整词命中与「效率提升指南」变体命中
    out = _tokens("效率提升 AI工具")
    assert "效率提升" in out
    assert "效率" in out
    assert "ai工具" in out


def test_tokens_ignores_punctuation_and_case() -> None:
    assert _tokens("AI, 工具！") == _tokens("ai 工具")


def test_tokens_drop_cross_script_bigrams() -> None:
    """跨中英边界的 2-gram 是噪声（真机验收打出来的：不清噪品牌契合恒 0.0x）。

    「AI工具」会切出「i工」这种永远不会出现在标题里的片段，留在表里只会
    撑大分母、稀释真实命中率。
    """
    out = _tokens("AI工具")
    assert "i工" not in out
    assert "ai" in out  # 纯英文段仍保留
    assert "工具" in out


def test_tokens_drop_long_whole_chunks() -> None:
    # 长句整体入表只撑分母：定位语几乎不可能整句出现在标题里
    out = _tokens("面向普通人的AI工具与效率方法")
    assert "工具与效率方法" not in out  # 7 字，超阈值
    assert "效率提升" in _tokens("效率提升")  # 4 字支柱要留住


def test_pillar_hit_outweighs_ordinary_word() -> None:
    """内容支柱是用户手选的取向声明，命中一个顶三个普通词。"""
    brand = {
        "positioning": "面向普通人的 AI 工具与效率方法",
        "audience": "上班族",
        "contentPillars": ["效率提升"],
    }
    kw, pl = brand_keywords(brand), pillar_keywords(brand)
    # 两边命中数量刻意接近（各 3 / 2 个 token），差别只该来自支柱加权
    pillar_hit = score_brand_fit(item("办公效率提升一倍"), kw, pl)
    ordinary_hit = score_brand_fit(item("上班族的一天"), kw, pl)
    assert pl and pl & _tokens("办公效率提升一倍")
    assert 1.0 > pillar_hit > ordinary_hit > 0.0
