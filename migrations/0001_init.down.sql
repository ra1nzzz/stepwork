-- 回滚 0001_init.sql
--
-- 升级中途失败时用户的库会停在半路，此前只能靠备份。顺序与 up 相反：
-- 先删索引（SQLite 不允许删掉仍被索引引用的列），再删列/表。
--
-- 这些脚本由 test_migration_rollback 的**真往返**验证（升到最新 → 逐步回滚
-- 到零 → 再升回最新）。写了但没跑过的 down 脚本等于没有。

DROP INDEX IF EXISTS idx_content_versions_project;
DROP INDEX IF EXISTS idx_jobs_type;
DROP INDEX IF EXISTS idx_jobs_state_lease;
DROP INDEX IF EXISTS idx_source_assets_hash;
DROP INDEX IF EXISTS idx_source_assets_project;
DROP INDEX IF EXISTS idx_content_projects_workspace;
DROP TABLE IF EXISTS content_versions;
DROP TABLE IF EXISTS jobs;
DROP TABLE IF EXISTS source_assets;
DROP TABLE IF EXISTS content_projects;
DROP TABLE IF EXISTS workspaces;
