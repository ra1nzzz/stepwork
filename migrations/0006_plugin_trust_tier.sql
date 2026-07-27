-- Migration: 0006_plugin_trust_tier.sql
-- Version:   0006
-- Upstream:  0005_brand_profiles.sql
-- Purpose:   PRD-PLG-004 插件信任分级（Official/Verified/Community/Experimental）
--            与 PRD-PLG-005 健康状态（最近测试时间/结果）。
--
--            installed_plugins 此前没有任何 trust/tier 列，UI 也就无从
--            「明确显示信任等级」；last_loaded_at 虽存在但从未被写入，
--            「最近测试时间」无处可存。
-- Spec:      PRD §8.9；ADR-009

-- 信任等级：安装时由 manifest.trustTier 声明，缺省 community。
-- 未使用 CHECK 约束，改由 handler 侧白名单校验（便于日后扩展等级）。
ALTER TABLE installed_plugins ADD COLUMN trust_tier TEXT NOT NULL DEFAULT 'community';

-- PRD-PLG-005「显示最近测试时间和错误」：健康检查各自独立于加载时间。
ALTER TABLE installed_plugins ADD COLUMN last_checked_at TEXT;
ALTER TABLE installed_plugins ADD COLUMN last_check_result TEXT;
