-- 回滚 0012_video_scenes.sql
--
-- 这些脚本由 test_migration_rollback 的**真往返**验证（升到最新 → 逐步回滚
-- 到零 → 再升回最新）。写了但没跑过的 down 脚本等于没有。
--
-- 顺序与 up 相反：先删索引（SQLite 不允许留着引用被删表的索引），再删表。

DROP INDEX IF EXISTS idx_video_scenes_version_seq;
DROP INDEX IF EXISTS idx_video_scenes_version;
DROP TABLE IF EXISTS video_scenes;
