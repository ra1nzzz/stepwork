# STEPWORK 项目知识库

> **本目录即项目知识库**（唯一权威来源）。代码外的所有决策、进度与来源，都在这里。
> **重定位后版本**：2026-09-08 · 短视频创作工厂 · 选题驱动 · Agent 原生

---

## 1. 阅读顺序（新接手必读）

```text
1. REPOSITIONING.md   ← 这是什么产品、六项不可违背原则、生态位
2. ROADMAP.md         ← 北极星、现在在做什么、接下来做什么
3. COMPLETED.md       ← 已经有什么（含「假实现」与「空壳目录」警示）
4. REFERENCE.md       ← 每个功能从哪来（仓库/调研/论文/文档）
5. HANDOFF-PROMPT.md  ← 交给编码 Agent 的执行提示词
```

---

## 2. 文件职责（严格执行，不得混淆）

| 文件 | 记录什么 | **不**记录什么 |
|---|---|---|
| `REPOSITIONING.md` | 产品定位、北极星、不可违背原则、生态位、目标架构 | 进度、排期 |
| `ROADMAP.md` | 北极星量化指标、**正在执行**的任务、**规划中**的模块 | 已完成项（移入 COMPLETED） |
| `COMPLETED.md` | 已完成的功能与模块（仅限真实现） | 计划、待办 |
| `REFERENCE.md` | 功能/模块的引用来源（调研、论文、仓库、文档、授权） | 实现细节 |
| `HANDOFF-PROMPT.md` | 交给编码 Agent 的上下文与约束 | 历史讨论 |

**漂移防护**：完成一项 → 从 ROADMAP 移到 COMPLETED；引用了外部来源 → 立即记入 REFERENCE。
**三者不重合**：同一件事只在一份文件里有权威记录，其余用链接指过去。

---

## 3. 知识库治理

使用 `consolidate-project-knowledge-base` SKILL（已安装于 `~/.workbuddy/skills/consolidate-project-knowledge-base/`）进行：

- **来源审计**：每份文档能否追溯到来源（`REFERENCE.md`）
- **重复 ID / 断链检查**：跨文档引用是否失效
- **过期镜像清理**：归档目录中的旧文档不得被当作执行依据
- **长程任务防漂移**：每个阶段结束时，比对 `ROADMAP` 的实际执行与规划差异，显式裁决（继续 / 回滚 / 改计划）

### 治理触发时机

| 时机 | 动作 |
|---|---|
| 完成一个阶段（S1–S8） | 更新 ROADMAP → COMPLETED，补充 REFERENCE |
| 引入外部依赖 | 立即登记 REFERENCE（含授权与风险） |
| 架构决策变更 | 新增 ADR（`adr/`）并在相关文档建链接 |
| 文档超过 30 天未更新 | 审计是否过期，标记或归档 |

> ⚠️ **长程任务漂移红线**：任何偏离 `REPOSITIONING.md` §3 六项原则的实现，视为漂移，必须显式裁决并回写文档后再继续。

---

## 4. ADR（架构决策记录）

`adr/` 目录保留**仍然有效**的技术决策（ADR-001~011）：

| ADR | 主题 |
|---|---|
| 001 | Tauri + React |
| 002 | Python sidecar |
| 003 | SQLite WAL |
| 004 | 命令总线 |
| 005 | Artifact-first |
| 006 | AGPL / Apache 双许可 |
| 007 | CLI / MCP 优先 |
| 008 | Publisher fill-preview |
| 009 | 插件独立进程（**V0.2，未实现**） |
| 010 | Media auto-pilot 迁移 |
| 011 | 编辑交换格式 |

> 产品定位类决策已被 `REPOSITIONING.md` 取代；新增 ADR 请遵循同目录命名规范。

---

## 5. 已归档（不再作为执行依据）

`archive/legacy/` 存放重定位前的规划文档，**仅供历史参考**：

```
PRODUCT_CHARTER.md  PRD.md  STRATEGY_PLAN.md  SYSTEM_SPEC.md
PHASE_PLAN.md  MVP_PLAN.md  ROADMAP.md（旧，版本路径叙事）
REFACTOR_PLAN.md  UPGRADE.md  DECISIONS.md
W1_MONOREPO_PLAN.md  W1_REVIEW.md  W2_REVIEW.md
W3_W4_PLAN.md  W3_W4_REVIEW.md  W5_PLAN.md  W5_REVIEW.md  W6_PLAN.md
LICENSE_AUDIT.md  MIGRATION_ASSESSMENT.md  PERF_BASELINE.md
settings_page_plan.md  README.md（旧）
```

→ 见 [`archive/legacy/README.md`](./archive/legacy/README.md)

**为什么归档**：这些文档基于「素材驱动的通用内容工作台」定位。产品已重定位为「选题驱动的短视频创作工厂」，继续照旧文档执行会导致目标漂移。

---

## 6. 快速索引

| 想找什么 | 去哪 |
|---|---|
| 产品是什么 | `REPOSITIONING.md` §1 |
| 不可违背的原则 | `REPOSITIONING.md` §3 |
| 在生态中的身份 | `REPOSITIONING.md` §4（ContentOps 域 Owner） |
| 真实代码在哪 | `COMPLETED.md` §1 + `REPOSITIONING.md` §6 |
| 哪些是假实现 | `COMPLETED.md` §3 ⛔ |
| 哪些目录是空的 | `COMPLETED.md` §4 |
| 现在该做什么 | `ROADMAP.md` §2「当前状态」+ §3（路线） |
| 能复用什么 | `REFERENCE.md` §2–3 |
| 有什么外部风险 | `REFERENCE.md` §4（StepFun 生图 2026-10-10 下线） |
