-- 回滚 0008_brand_reference_scripts.sql
--
-- 升级中途失败时用户的库会停在半路，此前只能靠备份。顺序与 up 相反：
-- 先删索引（SQLite 不允许删掉仍被索引引用的列），再删列/表。
--
-- 这些脚本由 test_migration_rollback 的**真往返**验证（升到最新 → 逐步回滚
-- 到零 → 再升回最新）。写了但没跑过的 down 脚本等于没有。

DROP INDEX IF EXISTS idx_pref_events_action;
DROP INDEX IF EXISTS idx_pref_events_profile;
DROP TABLE IF EXISTS user_preference_events;
DROP INDEX IF EXISTS idx_brand_ref_profile;
DROP TABLE IF EXISTS brand_reference_scripts;
