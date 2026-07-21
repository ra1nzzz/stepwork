# STEPWORK Migrations

本目录存放 STEPWORK SQLite 数据库的**顺序迁移文件**。所有数据库结构变更**必须**通过新增迁移文件完成，禁止直接修改数据库。

## 命名规则

```
NNNN_description.sql
```

- `NNNN`：四位数字，从 `0001` 开始**严格递增**，不允许跳号或重复
- `description`：小写蛇形，简短描述（如 `init`、`audit_events`、`agent_placeholder`、`add_brand_profile`）
- 文件编码：UTF-8（无 BOM）
- SQL 关键字大写，4 空格缩进

## 当前迁移清单

| 版本 | 文件 | 说明 | 状态 |
|---|---|---|---|
| 0001 | `0001_init.sql` | 5 张核心表（workspaces / content_projects / source_assets / jobs / content_versions） | ✅ W1 |
| 0002 | `0002_audit_events.sql` | 审计事件表 | ✅ W1 |
| 0003 | `0003_agent_placeholder.sql` | Agent / Publisher / Provenance 占位表 | ✅ W1 |

## 执行规则

1. **顺序执行**：迁移**必须**按版本号升序执行，不允许跳过
2. **永不修改已发布的迁移**：一旦迁移文件合入 `main` 并被任何环境应用，**禁止**修改。如需调整，新建迁移
3. **每个迁移独立事务**：单个迁移文件内部使用 `BEGIN; ... COMMIT;` 包裹，失败自动回滚
4. **每个迁移文件头部必须有注释**：版本号、上游版本、目的、规格引用

## 迁移文件模板

```sql
-- Migration: NNNN_<description>.sql
-- Version:   NNNN
-- Upstream:  <NNNN-1>_<prev_description>.sql
-- Purpose:   <一句话说明>
-- Spec:      SYSTEM_SPEC §<章节>

PRAGMA foreign_keys = ON;

BEGIN;

-- 你的 DDL 语句
CREATE TABLE example (
    id TEXT PRIMARY KEY,
    ...
);

COMMIT;
```

## 回滚策略

STEPWORK **不提供** `down` 迁移。回滚通过**备份恢复**实现：

1. 应用启动时、执行任何迁移前，自动备份 `stepwork.db` 到 `STEPWORK_HOME/backups/stepwork-<timestamp>.db`
2. 迁移失败：自动从最近备份恢复
3. 用户手动回滚：关闭应用 → 用备份文件替换 `stepwork.db` → 重启

**理由**：SQLite 的 `ALTER TABLE` 能力弱，down 迁移容易失败且难以测试。备份恢复更可靠。

## 版本追踪

数据库内维护 `schema_migrations` 表（由 Worker 启动时自动创建，不属于迁移文件）：

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    checksum    TEXT NOT NULL
);
```

Worker 启动时：

1. 读取 `schema_migrations` 中最大 `version`
2. 扫描 `migrations/` 目录，找到所有 `version > current` 的文件
3. 按升序逐个应用，每个迁移前自动备份
4. 全部成功后更新 `schema_migrations`

## 测试

每个迁移必须通过以下测试：

1. **空库应用**：在 `:memory:` 数据库从头执行所有迁移不报错
2. **顺序性**：任意中间版本升级到最新版本不报错
3. **外键完整性**：`PRAGMA foreign_key_check;` 返回空
4. **索引存在**：`sqlite_master` 中能找到所有声明的索引

测试代码位置：`worker/tests/test_migrations.py`（Week 2 实现）。

## 添加新迁移的流程

1. 在 `docs/adr/` 新建 ADR 说明变更理由（如涉及 Schema 演进）
2. 创建 `migrations/NNNN_<description>.sql`
3. 在本 README 的"当前迁移清单"中追加一行
4. 如变更涉及 Command/Artifact Envelope，同步更新 `schemas/`
5. 提交 PR，标题遵循 `feat(migrations): NNNN <description>`

## 禁止事项

- ❌ 修改已合入 `main` 的迁移文件
- ❌ 跳过版本号
- ❌ 在迁移中插入业务数据（DDL only，种子数据走 `scripts/seed_*.sql`）
- ❌ 在迁移中调用非确定性函数（如 `datetime('now')` 作为 DEFAULT，应用启动时填充）
