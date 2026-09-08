# 已归档文档（LEGACY）

> ⛔ **本目录下的文档不再作为执行依据。** 仅供历史追溯与决策考古。

## 为什么归档

2026-09-08，STEPWORK 完成重定位：

```text
旧：素材驱动的通用内容工作台（导入素材 → 分析 → 脚本 → 渲染 → 导出）
新：选题驱动的短视频创作工厂（发现选题 → 文案 → 配音 → 配图 → 渲染 → 发布）
```

这不是加功能，是**换入口**。旧规划文档（PRD / STRATEGY_PLAN / PRODUCT_CHARTER 等）均建立在旧定位之上，继续执行会导致目标漂移。

## 现行文档

| 用途 | 现在看这里 |
|---|---|
| 产品定位与原则 | [`../../REPOSITIONING.md`](../../REPOSITIONING.md) |
| 北极星、在办、规划 | [`../../ROADMAP.md`](../../ROADMAP.md) |
| 已完成功能 | [`../../COMPLETED.md`](../../COMPLETED.md) |
| 引用来源 | [`../../REFERENCE.md`](../../REFERENCE.md) |
| 知识库索引 | [`../../README.md`](../../README.md) |

## 归档清单与现状

| 文件 | 原用途 | 现状 |
|---|---|---|
| `PRODUCT_CHARTER.md` | 产品宪章 | **已被 REPOSITIONING.md 取代** |
| `PRD.md` | 需求文档 | 基于素材驱动，目标已变更 |
| `STRATEGY_PLAN.md` | 战略规划 | 定位已变更 |
| `SYSTEM_SPEC.md` | 系统规格 | 架构描述部分仍有效，但定位已变 |
| `ROADMAP.md` | 版本路径（V0.1→V1.0） | 被新 ROADMAP 取代（职责改为北极星+在办+规划） |
| `PHASE_PLAN.md` / `MVP_PLAN.md` | 排期 | 被新 ROADMAP §3（S0–S8）取代 |
| `REFACTOR_PLAN.md` / `UPGRADE.md` | 重构计划 | 历史记录 |
| `W1_MONOREPO_PLAN.md` / `W1_REVIEW.md` / `W2_REVIEW.md` / `W3_W4_PLAN.md` / `W3_W4_REVIEW.md` / `W5_PLAN.md` / `W5_REVIEW.md` / `W6_PLAN.md` | 周计划与评审 | 历史记录；其中的技术结论仍然有效 |
| `DECISIONS.md` | 决策记录 | 架构决策见 `../../adr/`；产品决策见 `REPOSITIONING.md` |
| `LICENSE_AUDIT.md` | 许可审计 | 占位版，需重做（ffmpeg 构建授权待确认） |
| `MIGRATION_ASSESSMENT.md` | 迁移评估 | 历史记录 |
| `PERF_BASELINE.md` | 性能基线 | 历史记录 |
| `settings_page_plan.md` | 设置页计划 | 历史记录 |
| `README.md` | 旧文档索引 | 已由 `../../README.md` 取代 |

## 可考古的内容

- **技术决策**：ADR-001~011 未归档（仍有效），在 `../../adr/`
- **工程实践**：W1–W6 评审中的真往返迁移测试、命令响应契约、设计系统等结论，已被 `COMPLETED.md` 继承
- **历史教训**：「改后端字段前端静默失效」「插件系统只有注册表 CRUD」等问题，在 `COMPLETED.md` 与 `ROADMAP.md` 中已标注

---

> 本文件原为「STEPWORK 规划文档包」索引（PRD / SYSTEM_SPEC / MVP_PLAN / PHASE_PLAN），现已废止原用途。
