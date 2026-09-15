-- 回滚 0015_audit_workspace_scope.sql
--
-- 顺序与 up 相反：先删索引，再删列（SQLite 3.35+ 才支持 DROP COLUMN；
-- 更早的走 table rebuild，本项目 sqlite 版本要求见 pyproject）。
--
-- 这些脚本由 test_migration_rollback 的**真往返**验证（升到最新 → 逐步回滚
-- 到零 → 再升回最新）。写了但没跑过的 down 脚本等于没有。

DROP INDEX IF EXISTS idx_approval_workspace;
DROP INDEX IF EXISTS idx_audit_workspace;
ALTER TABLE approval_requests DROP COLUMN workspace_id;
ALTER TABLE audit_events DROP COLUMN workspace_id;
