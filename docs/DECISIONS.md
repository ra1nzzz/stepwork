# STEPWORK 决策记录（Decisions Log）

本文档固化项目的关键决策，作为开发过程中一致性判断的单一事实源。所有决策**已冻结**，除非通过 ADR 流程显式修订。

**详细架构决策记录（ADR）**：见 [adr/](./adr/) 目录。

---

## D1 · 任务状态机以 SYSTEM_SPEC §10.1 为准

**日期**：2026-07-21
**状态**：Accepted
**来源**：STRATEGY_PLAN §1.2 第 1 项

### 决策

任务状态机以 `SYSTEM_SPEC.md §10.1` 为准：**9 个主状态 + 9 个业务阶段**。

- **主状态**：`PENDING / READY / RUNNING / WAITING_EXTERNAL / WAITING_USER / RETRY_SCHEDULED / COMPLETED / FAILED_FINAL / CANCELLED`
- **业务阶段**：`DOWNLOADING / TRANSCRIBING / ANALYZING / DELEGATING / GENERATING / SYNTHESIZING / RENDERING / PUBLISHING / VERIFYING`

`PRODUCT_CHARTER.md §8.4` 列出的 15 个细粒度状态仅作为**产品叙事**，不用于开发实现。

### 理由

两文档存在不一致。SYSTEM_SPEC 作为技术基线优先，避免开发期双源漂移。状态枚举已在 `schemas/job-state.enum.json` 与 `schemas/job-stage.enum.json` 冻结。

---

## D2 · 文件目录采用 Workspace 嵌套结构

**日期**：2026-07-21
**状态**：Accepted
**来源**：STRATEGY_PLAN §1.2 第 2 项

### 决策

本地文件目录采用 **`STEPWORK_HOME/workspaces/<workspace-id>/projects/<project-id>/`** 嵌套结构，而非 `STEPWORK_HOME/projects/<project-id>/`。

### 理由

为 V0.4 多 Workspace 预留扩展空间。CHARTER 中的扁平结构仅覆盖单 Workspace 场景，嵌套结构可在不破坏现有项目的前提下引入多 Workspace。

---

## D3 · 周期估算以 PHASE_PLAN 为准

**日期**：2026-07-21
**状态**：Accepted
**来源**：STRATEGY_PLAN §1.2 第 3 项

### 决策

版本周期估算以 `PHASE_PLAN.md` 为准：

- V0.1 约 8 周
- V1.0 累计 9-12 个月（单人）或 6-8 个月（团队）
- **当前按单人资源规划：V1.0 = 10-12 个月**

`PRODUCT_CHARTER.md` 中的"V0.1 约 4-6 周，V1.0 累计 5-8 个月"过于乐观，已弃用。

### 理由

避免不切实际的 deadline 压力导致的范围削减或质量妥协。

---

## D4 · Prototype 仅作桌面视觉基线

**日期**：2026-07-21
**状态**：Accepted
**来源**：STRATEGY_PLAN §1.2 第 4 项

### 决策

`Prototype/` 目录仅作为**桌面深色模式视觉基线**（min-width 1120px）。**移动端不在 V0.1 范围**。

### 理由

DESIGN-MANIFEST 承诺的 360-1920 全视口矩阵与实际 Prototype CSS（`body { min-width: 1120px }`）不符。MVP 阶段明确"桌面优先"，在 PRD 中已声明移动端不在 V0.1 范围。

---

## D5 · Command Bus 宿主为 Python Worker

**日期**：2026-07-21
**状态**：Accepted
**来源**：W1_MONOREPO_PLAN v1.1 Patch-A2

### 决策

**Command Bus 宿主为 Python Worker**，Tauri Rust Host 仅负责：

- Sidecar 生命周期管理（启动 / 停止 / 重启）
- Capabilities 与权限
- 系统凭据存储
- 文件选择对话框

**所有业务 Command 一律转发到 Python Worker** 处理。

### 理由

核心业务逻辑、数据模型、Job Engine 都在 Python 侧（pydantic / SQLAlchemy 生态），符合 SYSTEM_SPEC §3.3"本地业务层 Python 3.12"的定位。Tauri Rust 保持薄壳，避免双语言业务逻辑重复实现。

### 影响

- `worker/runtime/handlers/` 必须实现 Command Bus 入口
- `apps/desktop/src-tauri/src/commands/` 仅做透明转发，不实现业务规则
- SYSTEM_SPEC §4 图示"Tauri Rust Host → Application Gateway"解读为**逻辑分层**，不是**进程归属**

---

## 决策修订

如需修订上述任一决策：

1. 新建 ADR（`docs/adr/ADR-NNN-<slug>.md`）标记为 `Supersedes D<N>`
2. 经 GOVERNANCE.md 定义的流程批准
3. 更新本文档并在旧决策上标注 `Superseded by ADR-NNN`
