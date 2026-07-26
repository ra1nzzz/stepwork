-- 0010: 定时发布队列
--
-- 背景：用户要「定时发布」但**明确不要完全自动发布**（ADR-008 也禁止自动
-- 点击最终发布）。这两件事能同时成立，靠的是区分两种模式：
--
--   platform_native —— 平台自己有定时发布（抖音 2h~7d、B站 ≤24h、
--     小红书 ≤7d）。我们把目标时间填进平台自己的定时字段，用户确认提交一次，
--     之后由**平台**在到点时发布。全程没有任何自动点击，却是真正的无人值守。
--
--   local_reminder —— 平台没有原生定时（如视频号）。此时本地队列到点只能
--     **备好填充包并提醒用户**，不能替他点发布。这是提醒，不是自动发布，
--     字段名和 UI 文案都必须如实说明，不能包装成「定时发布」。
--
-- status 取值：pending / armed（已交给平台或已提醒）/ done / cancelled / failed

CREATE TABLE scheduled_publishes (
    id                TEXT PRIMARY KEY,
    workspace_id      TEXT NOT NULL,
    project_id        TEXT NOT NULL REFERENCES content_projects(id) ON DELETE CASCADE,
    variant_id        TEXT NOT NULL REFERENCES platform_variants(id) ON DELETE CASCADE,
    platform          TEXT NOT NULL,
    -- 目标发布时间（ISO8601，UTC）
    scheduled_at      TEXT NOT NULL,
    -- platform_native / local_reminder，见上方说明
    mode              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    -- 内容哈希：到点时若变体已改动，说明用户排的不是这份内容，需重新确认
    content_hash      TEXT NOT NULL,
    note              TEXT,
    -- 到点触发（提醒或交付填充包）的时间
    fired_at          TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX idx_scheduled_publishes_due
    ON scheduled_publishes(status, scheduled_at);
CREATE INDEX idx_scheduled_publishes_project
    ON scheduled_publishes(project_id);
