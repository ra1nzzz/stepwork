"""平台发布约束与填充包（PRD-PUB-003，遵循 ADR-008 FILL_AND_PREVIEW）。

ADR-008 决定：V0.1–V0.5 **只支持自动填写 + 停在预览页**，最终「发布」
必须由用户手动点击。因此本模块的产出是一个**填充包**（fill package）：
把变体内容规整成 publisher 插件 / 浏览器扩展可直接消费的结构，并在提交
之前按平台规则校验（标题超长、标签过多这类问题，等填到页面上才发现就晚了）。

明确不做（ADR-008 / SYSTEM_SPEC §14.6 显式禁止）：
- 自动点击最终发布
- 指纹伪装 / 反检测 / 多账号轮换规避风控

真正驱动浏览器 DOM 的那一层属于 publisher 插件（需要用户已登录的浏览器
会话），不在 worker 内实现；worker 只负责产出**内容与约束**。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple


class PlatformRules(NamedTuple):
    """一个平台的发布字段约束与定时发布能力。"""

    id: str
    label: str
    title_max: int
    body_max: int
    tag_max_count: int
    tag_max_len: int
    #: 平台是否要求必须有视频文件
    requires_video: bool
    #: 平台原生定时发布最多可提前的天数；0 = 平台没有原生定时能力
    schedule_max_ahead_days: int = 0
    #: 平台要求的最小提前量（分钟）。抖音要求至少提前 2 小时
    schedule_min_lead_minutes: int = 0
    #: 使用平台原生定时的前置条件/坑，原样透出给用户（这些坑不说清楚，
    #: 用户会以为排上了、实际根本没生效）
    schedule_note: str = ""


#: 平台规则表。数值取各平台公开的常见上限，作为**提交前预校验**用；
#: 平台随时可能调整，故校验失败给的是可读提示而非硬性断言。
PLATFORM_RULES: dict[str, PlatformRules] = {
    "douyin": PlatformRules(
        id="douyin",
        label="抖音",
        title_max=30,
        body_max=1000,
        tag_max_count=5,
        tag_max_len=20,
        requires_video=True,
        schedule_max_ahead_days=7,
        schedule_min_lead_minutes=120,
        schedule_note=(
            "需提前至少 2 小时、最多 7 天；定时入口在手机端创作者中心，"
            "网页版没有；视频仍需过审"
        ),
    ),
    "bilibili": PlatformRules(
        id="bilibili",
        label="B站",
        title_max=80,
        body_max=2000,
        tag_max_count=10,
        tag_max_len=20,
        requires_video=True,
        # B站的定时窗口明显更短，只有约 24 小时
        schedule_max_ahead_days=1,
        schedule_min_lead_minutes=0,
        schedule_note=(
            "定时窗口约 24 小时内；稿件仍需过审，审核不通过则不会按时发布，"
            "别卡点上传"
        ),
    ),
    "xiaohongshu": PlatformRules(
        id="xiaohongshu",
        label="小红书",
        title_max=20,
        body_max=1000,
        tag_max_count=10,
        tag_max_len=20,
        requires_video=False,
        schedule_max_ahead_days=7,
        schedule_min_lead_minutes=0,
        schedule_note="「定时发布」入口通常仅专业号/企业号可见，普通号看不到该功能",
    ),
    "weixin_channels": PlatformRules(
        id="weixin_channels",
        label="微信视频号",
        title_max=30,
        body_max=1000,
        tag_max_count=10,
        tag_max_len=20,
        requires_video=True,
        # 官方定时能力不稳定且设定后不可修改，按「无原生定时」处理更诚实：
        # 与其让用户以为排上了，不如明确走本地提醒
        schedule_max_ahead_days=0,
        schedule_note="官方定时能力不稳定且设定后无法修改，本地按到点提醒处理",
    ),
    "generic": PlatformRules(
        id="generic",
        label="通用",
        title_max=100,
        body_max=5000,
        tag_max_count=20,
        tag_max_len=40,
        requires_video=False,
    ),
}

#: ADR-008：唯一支持的自动化程度
FILL_MODE = "fill_and_preview"

#: 定时发布的两种模式。区分它们是整个设计的关键 —— 只有前者是真正的
#: 无人值守，后者只是到点提醒，绝不能混为一谈（见 0010 迁移的说明）。
#:
#: platform_native：把时间填进**平台自己的定时字段**，用户确认提交一次，
#:   之后由平台在到点时发布。全程零自动点击，却真的无人值守 —— 这正是
#:   「不做完全自动发布」与「要定时发布」能同时成立的地方。
SCHEDULE_NATIVE = "platform_native"
#: local_reminder：平台没有原生定时。本地到点只能备好填充包并提醒用户，
#:   **不会**替他点发布。命名上刻意叫 reminder 而不是 schedule，避免 UI 把
#:   它包装成「定时发布」让用户以为可以睡觉。
SCHEDULE_LOCAL = "local_reminder"


def supports_native_schedule(rules: PlatformRules) -> bool:
    """该平台是否有可用的原生定时发布。"""
    return rules.schedule_max_ahead_days > 0


def validate_schedule(
    rules: PlatformRules, scheduled_at: datetime, now: datetime
) -> list[dict[str, Any]]:
    """校验目标时间是否落在平台原生定时窗口内。

    返回问题列表而非抛异常：超窗不是错误，只是要**降级成本地提醒**并把
    原因说清楚（用户可能就是想排 30 天后）。
    """
    issues: list[dict[str, Any]] = []
    if scheduled_at <= now:
        issues.append(
            {
                "field": "scheduled_at",
                "level": "error",
                "message": "定时时间必须晚于当前时间",
            }
        )
        return issues
    if not supports_native_schedule(rules):
        return issues

    lead = scheduled_at - now
    if lead < timedelta(minutes=rules.schedule_min_lead_minutes):
        issues.append(
            {
                "field": "scheduled_at",
                "level": "warning",
                "message": (
                    f"{rules.label}要求至少提前 "
                    f"{rules.schedule_min_lead_minutes // 60} 小时，当前提前量不足，"
                    f"将改为本地到点提醒"
                ),
            }
        )
    if lead > timedelta(days=rules.schedule_max_ahead_days):
        issues.append(
            {
                "field": "scheduled_at",
                "level": "warning",
                "message": (
                    f"{rules.label}最多支持提前 {rules.schedule_max_ahead_days} 天，"
                    f"超出部分无法用平台原生定时，将改为本地到点提醒"
                ),
            }
        )
    return issues


def resolve_schedule_mode(
    rules: PlatformRules, scheduled_at: datetime, now: datetime
) -> str:
    """决定用平台原生定时还是本地提醒。

    只有「平台支持 + 目标时间落在窗口内」才用原生；其余一律降级为本地提醒。
    降级是**保守**方向：宁可提醒用户来点一下，也不能声称排上了却没排上。
    """
    if not supports_native_schedule(rules):
        return SCHEDULE_LOCAL
    lead = scheduled_at - now
    if lead < timedelta(minutes=rules.schedule_min_lead_minutes):
        return SCHEDULE_LOCAL
    if lead > timedelta(days=rules.schedule_max_ahead_days):
        return SCHEDULE_LOCAL
    return SCHEDULE_NATIVE


def build_schedule_block(
    rules: PlatformRules, scheduled_at: datetime, now: datetime
) -> dict[str, Any]:
    """填充包里的 ``schedule`` 段，供 publisher 插件消费。"""
    mode = resolve_schedule_mode(rules, scheduled_at, now)
    return {
        "mode": mode,
        "scheduled_at": scheduled_at.isoformat(),
        # 原生模式下插件要去填平台自己的定时字段；本地模式下插件什么都不做
        "fill_native_field": mode == SCHEDULE_NATIVE,
        "platform_window": {
            "supported": supports_native_schedule(rules),
            "max_ahead_days": rules.schedule_max_ahead_days,
            "min_lead_minutes": rules.schedule_min_lead_minutes,
        },
        "note": rules.schedule_note,
        # 即便走原生定时，提交动作仍必须由用户点（ADR-008 不因定时而放宽）
        "requires_manual_submit": True,
        "issues": validate_schedule(rules, scheduled_at, now),
    }


def resolve_rules(platform: str) -> PlatformRules:
    """取平台规则；未知平台回落 generic（不阻断，但会在包里注明）。"""
    return PLATFORM_RULES.get(platform, PLATFORM_RULES["generic"])


def validate_fields(
    rules: PlatformRules,
    *,
    title: str,
    body: str,
    tags: list[str],
    has_video: bool,
) -> list[dict[str, Any]]:
    """按平台规则校验字段，返回问题列表（空 = 通过）。

    返回「问题」而不是抛异常：填充包本身仍要产出，让用户看到预览并自行
    决定是否调整——这与 FILL_AND_PREVIEW 的人工确认精神一致。
    """
    issues: list[dict[str, Any]] = []
    if not title.strip():
        issues.append({"field": "title", "level": "error", "message": "标题不能为空"})
    elif len(title) > rules.title_max:
        issues.append(
            {
                "field": "title",
                "level": "error",
                "message": f"标题 {len(title)} 字，超过{rules.label}上限 {rules.title_max} 字",
            }
        )
    if len(body) > rules.body_max:
        issues.append(
            {
                "field": "body",
                "level": "error",
                "message": f"正文 {len(body)} 字，超过{rules.label}上限 {rules.body_max} 字",
            }
        )
    if len(tags) > rules.tag_max_count:
        issues.append(
            {
                "field": "tags",
                "level": "warning",
                "message": (
                    f"标签 {len(tags)} 个，超过{rules.label}上限 "
                    f"{rules.tag_max_count} 个，多余的可能被平台忽略"
                ),
            }
        )
    for tag in tags:
        if len(tag) > rules.tag_max_len:
            issues.append(
                {
                    "field": "tags",
                    "level": "warning",
                    "message": f"标签「{tag[:10]}…」超过 {rules.tag_max_len} 字",
                }
            )
    if rules.requires_video and not has_video:
        issues.append(
            {
                "field": "video",
                "level": "error",
                "message": f"{rules.label}需要视频文件，请先完成渲染",
            }
        )
    return issues


def build_fill_package(
    *,
    variant: dict[str, Any],
    video_path: str | None,
    cover_path: str | None,
    scheduled_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """构造供 publisher 插件消费的填充包。

    ``auto_publish`` 恒为 ``False`` 且随包下发 —— 消费方（插件/扩展）据此
    知道自己**不得**点击最终发布按钮（ADR-008）。
    """
    rules = resolve_rules(str(variant.get("platform") or ""))
    title = str(variant.get("title") or "")
    body = str(variant.get("body") or "")
    tags = [str(t) for t in (variant.get("tags") or [])]
    issues = validate_fields(
        rules, title=title, body=body, tags=tags, has_video=bool(video_path)
    )
    schedule = (
        build_schedule_block(rules, scheduled_at, now or datetime.now(UTC))
        if scheduled_at is not None
        else None
    )
    return {
        "platform": rules.id,
        "platform_label": rules.label,
        # ADR-008：只填写 + 预览，绝不自动发布
        "mode": FILL_MODE,
        "auto_publish": False,
        "requires_manual_publish": True,
        "fields": {"title": title, "body": body, "tags": tags},
        "assets": {"video": video_path, "cover": cover_path},
        "constraints": {
            "title_max": rules.title_max,
            "body_max": rules.body_max,
            "tag_max_count": rules.tag_max_count,
        },
        # 定时发布（None = 立即发布）。原生模式下插件负责把时间填进平台
        # 自己的定时字段；本地模式下由 worker 到点提醒，插件不参与。
        "schedule": schedule,
        "issues": issues,
        # 有 error 级问题时不建议提交给插件（warning 可继续）
        "ready": not any(i["level"] == "error" for i in issues)
        and not any(
            i["level"] == "error" for i in (schedule or {}).get("issues", [])
        ),
    }
