-- Migration: 0008_brand_reference_scripts.sql
-- Version:   0008
-- Upstream:  0007_project_tags_access.sql
-- Purpose:   PRD-BRD-003「导入历史脚本，可检索并用于风格参考」与
--            PRD-BRD-004「记录用户接受、修改和删除偏好」。
--
--            brand_profiles 此前只有 5 个标量字段，format_brand_prompt_block
--            也只注入这些标量——没有任何范文，「风格参考」无从谈起；
--            用户对 AI 产出的采纳/改写/丢弃也完全没有记录。
-- Spec:      PRD §8.5

-- 历史脚本范文：按 BrandProfile 归属，供检索与 prompt 风格参考
CREATE TABLE IF NOT EXISTS brand_reference_scripts (
    id                TEXT PRIMARY KEY,
    brand_profile_id  TEXT NOT NULL REFERENCES brand_profiles(id) ON DELETE CASCADE,
    title             TEXT NOT NULL DEFAULT '',
    content           TEXT NOT NULL,
    source            TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_brand_ref_profile
    ON brand_reference_scripts(brand_profile_id);

-- 用户偏好事件（PRD-BRD-004，P2）：记录对 AI 产出的采纳/改写/丢弃。
-- 供后续个性化建议使用；本身不参与生成逻辑。
CREATE TABLE IF NOT EXISTS user_preference_events (
    id                TEXT PRIMARY KEY,
    brand_profile_id  TEXT REFERENCES brand_profiles(id) ON DELETE SET NULL,
    project_id        TEXT REFERENCES content_projects(id) ON DELETE CASCADE,
    version_id        TEXT,
    action            TEXT NOT NULL,
    detail            TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pref_events_profile
    ON user_preference_events(brand_profile_id);
CREATE INDEX IF NOT EXISTS idx_pref_events_action
    ON user_preference_events(action);
