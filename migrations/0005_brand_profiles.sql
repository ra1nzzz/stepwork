-- Migration: 0005_brand_profiles.sql
-- Version:   0005
-- Upstream:  0004_plugin_registry.sql
-- Purpose:   Tranche 2 (PRD P0 补全)：
--            1. brand_profiles 表（PRD-BRD-001/002）——品牌画像 CRUD 与
--               生成注入（generate_topic / generate_script）的存储层。
--            2. audit_events 追加 event_type / payload 两列（费用透明：
--               event_type='provider_invocation' 审计行需要结构化 payload；
--               0002 原表无此二列，SQLite ADD COLUMN 追加，向后兼容）。
--            3. platform_variants 追加 project_id / video_version_id 两列
--               （发布 MVP：variant 按项目组织并可选绑定渲染产物版本；
--               0003 原表以 content_version_id 为唯一锚点，缺项目维度）。
--            注：workspaces.archived_at 已在 0001 存在，此处不再补列。
-- Spec:      PRD-BRD-001/002, PRD-PUB-001/002, SYSTEM_SPEC §8.1, §15.5

PRAGMA foreign_keys = ON;

CREATE TABLE brand_profiles (
    id                  TEXT PRIMARY KEY,
    workspace_id        TEXT NOT NULL REFERENCES workspaces(id),
    name                TEXT NOT NULL,
    positioning         TEXT DEFAULT '',
    audience            TEXT DEFAULT '',
    tone                TEXT DEFAULT '',
    content_pillars     TEXT DEFAULT '[]', -- JSON array
    banned_expressions  TEXT DEFAULT '[]', -- JSON array
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX idx_brand_profiles_workspace ON brand_profiles(workspace_id);

ALTER TABLE audit_events ADD COLUMN event_type TEXT;
ALTER TABLE audit_events ADD COLUMN payload TEXT NOT NULL DEFAULT '{}';

ALTER TABLE platform_variants ADD COLUMN project_id TEXT REFERENCES content_projects(id);
ALTER TABLE platform_variants ADD COLUMN video_version_id TEXT REFERENCES content_versions(id);
CREATE INDEX idx_platform_variants_project ON platform_variants(project_id);
