# ADR-003: 本地存储采用 SQLite WAL 模式

- **Status**: Accepted
- **Date**: 2026-07-21
- **Deciders**: @ra1nzzz

## Context

STEPWORK 是本地优先应用，需要选择嵌入式数据库。候选：SQLite、DuckDB、LevelDB、自研文件格式。

关键考量：
- 事务完整性（Job 状态、ContentVersion 链）
- 并发读（UI 读 + Worker 写）
- 单文件便于备份/迁移
- 成熟稳定（项目长达 10-12 个月）
- 不需要全文搜索的高级特性（FTS5 够用）

## Decision

采用 **SQLite + WAL（Write-Ahead Logging）模式**。

- 数据库文件：`STEPWORK_HOME/stepwork.db`
- 启用 `PRAGMA journal_mode = WAL` 与 `PRAGMA foreign_keys = ON`
- 所有迁移使用版本号管理（`migrations/NNNN_*.sql`）
- 大体积视频二进制不存数据库，仅存文件路径 + SHA-256

## Consequences

**正面**：
- WAL 模式支持单写多读，UI 不会被 Worker 长任务阻塞
- 单文件备份友好（直接复制 `stepwork.db` + WAL 文件）
- 跨平台稳定（SQLite 是全球部署最广的数据库）
- 工具链成熟（SQLAlchemy、Alembic、DB Browser）

**负面**：
- 写入吞吐量受单写者限制（但 STEPWORK 是单用户应用，非问题）
- 全文搜索能力弱于专业引擎（FTS5 已够用，未来如需升级可换 tantivy）

**关联**：migrations/0001_init.sql 已启用 WAL；SYSTEM_SPEC §9.1 明确此决策。
