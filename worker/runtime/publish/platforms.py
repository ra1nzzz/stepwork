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

from typing import Any, NamedTuple


class PlatformRules(NamedTuple):
    """一个平台的发布字段约束。"""

    id: str
    label: str
    title_max: int
    body_max: int
    tag_max_count: int
    tag_max_len: int
    #: 平台是否要求必须有视频文件
    requires_video: bool


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
        "issues": issues,
        # 有 error 级问题时不建议提交给插件（warning 可继续）
        "ready": not any(i["level"] == "error" for i in issues),
    }
