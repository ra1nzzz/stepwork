-- Migration: 0007_project_tags_access.sql
-- Version:   0007
-- Upstream:  0006_plugin_trust_tier.sql
-- Purpose:   PRD-WS-003「项目搜索、标签与最近访问」。
--
--            content_projects 此前无 tags 列、无最近访问时间：
--            - 前端「搜索」只是对已拉取列表做 title 本地 includes；
--            - ListProjects 无任何查询参数；
--            - 首页「最近项目」实为 created_at 倒序（updated_at 建完就不再变）。
--            本迁移补齐存储，查询能力由 ListProjects 参数化提供。
-- Spec:      PRD §8.1

-- 标签：JSON 字符串数组（与 metadata 等 JSON 列同风格，避免引入关联表）
ALTER TABLE content_projects ADD COLUMN tags TEXT NOT NULL DEFAULT '[]';

-- 最近访问时间：由 GetProject（打开项目）刷新，与 updated_at（内容变更）分开
ALTER TABLE content_projects ADD COLUMN last_accessed_at TEXT;

CREATE INDEX IF NOT EXISTS idx_projects_last_accessed
    ON content_projects(last_accessed_at);
