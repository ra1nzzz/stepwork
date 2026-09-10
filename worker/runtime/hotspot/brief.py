"""热点 → 选题简报（``hotspot_brief``）。

**为什么是「简报」而不是「选题」**：热点是**外部未核实**的内容（PRD-AGT-003），
它不能冒充已验证素材直接产出选题。所以这一步只做一件事：把一条热点**原样**
连同它的出处和信任等级，装订成一份可以被下游消费的素材包。真正下判断
（从素材里提炼差异化角度）交给既有的 ``GenerateTopic`` —— 那条链路一个字没改。

**为什么纯函数**：简报必须可复盘。掺进 IO，想验证「出来的长什么样」就只能
真跑一遍调命令看输出；抽成纯函数后，给一条 ``HotspotItem`` 就断言得出来。

**为什么理由/打分分解要调用方回传**：它们是 ``RecommendHotspots`` 那一刻的
判断结果，不是热点事实（发现取事实、推荐下判断，两者分离是本域的既定架构）。
转换命令**不重算、也不猜**：调用方手上当时展示的是哪份，就带哪份过来。
没带就在出参里如实写 ``reason_source="none"``，不假装有。
"""

from __future__ import annotations

from typing import Any

# 信任等级与复核状态复用出站 Agent 通道的常量 —— 定义方
# ``agents/channel.py`` 已因「同一常量在 4 个文件各写一份」吃过亏（改了一处，
# UI 复核入口就漏掉一类产物，且不报错）。热点同样属于「外部拿回的、未经复核
# 的内容」，共用同一词表，UI 才能用同一套逻辑筛出所有待复核产物。
from worker.runtime.agents.channel import REVIEW_STATE, TRUST_LEVEL
from worker.runtime.hotspot.models import HotspotItem

#: 简报正文的类型标记（写进 ``content_versions.content_type``）
HOTSPOT_BRIEF_CONTENT_TYPE = "hotspot_brief"

#: 简报首行的免责头。**必须留在正文里**而不是只写进 producer：
#: 下游 ``GenerateTopic`` 读的是 ``content`` 文本，把限制写在这里，
#: 无论谁来消费（AI 提示词、人工阅读、导出）都躲不开。
_UNVERIFIED_HEADER = (
    "【外部热点素材 · 未经事实核实】\n"
    "以下内容摘自公开热榜，本系统**未核实其真实性**，引用数据/结论前请自行查证。\n"
)


def _fmt_score(value: float | None) -> str:
    if value is None:
        return "未提供"
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def _fmt_breakdown(breakdown: dict[str, Any] | None) -> str:
    """把打分分解渲染成一行。**只认已知的六维**，多出来的键一概忽略 ——
    上游加维度时这里不会渲染出半张表，也不会因为未知键而崩。
    """
    if not breakdown:
        return ""
    labels = (
        ("freshness", "时效"),
        ("heat", "热度"),
        ("brandFit", "品牌契合"),
        ("novelty", "新颖"),
        ("feedback", "反馈"),
        ("penalty", "惩罚"),
    )
    parts: list[str] = []
    for key, label in labels:
        raw = breakdown.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        parts.append(f"{label} {float(raw):.2f}")
    return " / ".join(parts)


def build_brief(
    item: HotspotItem,
    *,
    reason: str | None = None,
    reason_source: str | None = None,
    breakdown: dict[str, Any] | None = None,
) -> str:
    """把一条热点装订成「选题简报」正文（确定性，无 IO）。

    Args:
        item: 热点事实。
        reason: 调用方当时展示给用户的那句推荐理由；没带就不编。
        reason_source: ``"ai"`` / ``"rule"`` / ``"human"``；缺省按 ``"none"``
            处理 —— 降级必须可见（模板句不能冒充 AI 洞见，反之亦然）。
        breakdown: ``RecommendHotspots`` 出参里的 ``breakdown``，原样带入。

    Returns:
        可读文本，含未核实声明 + 事实字段 + （可选的）入选理由与打分分解。
    """
    lines: list[str] = [_UNVERIFIED_HEADER, f"标题：{item.title}"]
    lines.append(f"来源：{item.source}")
    if item.url:
        lines.append(f"链接：{item.url}")
    lines.append(f"发布时间：{item.published_at or '未提供'}")
    lines.append(f"热度值：{_fmt_score(item.score)}（仅同源内可比，跨源不可比）")
    if item.summary:
        lines.append(f"摘要：{item.summary}")
    lines.append(f"抓取批次：{item.batch_id or '未知'}（发现于 {item.discovered_at or '未知'}）")

    if reason:
        origin = reason_source or "none"
        lines.append("")
        lines.append(f"入选理由（{origin}）：{reason}")

    breakdown_line = _fmt_breakdown(breakdown)
    if breakdown_line:
        lines.append(f"打分分解：{breakdown_line}")

    return "\n".join(lines) + "\n"


def brief_producer(
    item: HotspotItem,
    *,
    reason_source: str | None = None,
    breakdown: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """简报的 ``producer`` 字段 —— **来源与信任等级的落库位置**（PRD-AGT-003）。

    ``content_versions`` 只有 ``producer`` 这一个自由字段，信任等级就落这里。
    UI 的「待复核」列表按 ``producer.trustLevel`` 筛，与出站 Agent 产物同一套。
    """
    producer: dict[str, Any] = {
        "kind": "hotspot-brief",
        "trustLevel": TRUST_LEVEL,
        "reviewState": REVIEW_STATE,
        # 出处三件套：没有它们，「外部结果具备来源」就只是句口号
        "hotspotId": item.id,
        "hotspotSource": item.source,
        "sourceUrl": item.url or None,
        "batchId": item.batch_id,
        "discoveredAt": item.discovered_at,
        "reasonSource": reason_source or "none",
    }
    if breakdown:
        # 原样留档：转换本身不该改写判断结果，否则复盘时对不上推荐页看到的数
        producer["breakdown"] = dict(breakdown)
    return producer
