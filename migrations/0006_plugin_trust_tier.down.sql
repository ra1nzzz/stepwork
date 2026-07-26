-- 回滚 0006_plugin_trust_tier.sql
--
-- 升级中途失败时用户的库会停在半路，此前只能靠备份。顺序与 up 相反：
-- 先删索引（SQLite 不允许删掉仍被索引引用的列），再删列/表。
--
-- 这些脚本由 test_migration_rollback 的**真往返**验证（升到最新 → 逐步回滚
-- 到零 → 再升回最新）。写了但没跑过的 down 脚本等于没有。

ALTER TABLE installed_plugins DROP COLUMN last_check_result;
ALTER TABLE installed_plugins DROP COLUMN last_checked_at;
ALTER TABLE installed_plugins DROP COLUMN trust_tier;
