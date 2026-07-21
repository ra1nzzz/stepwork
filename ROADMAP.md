# STEPWORK Roadmap

本文档提供 STEPWORK 的版本路径概览。**详细排期与里程碑以 [docs/PHASE_PLAN.md](./docs/PHASE_PLAN.md) 为准**。

## 版本路径

```text
V0.1 Alpha       V0.2 Beta        V0.5 RC          V1.0 GA
   │                │                │                │
   ├─ MVP 闭环      ├─ 插件 SDK      ├─ 插件市场      ├─ 完整 Agent 互操作
   ├─ 创作闭环      ├─ 多 Workspace  ├─ Publisher 正式 ├─ 完整 Provenance
   ├─ CLI           ├─ 主题系统      ├─ A2A Server    ├─ 插件签名
   ├─ MCP 演示      ├─ 协作基础      ├─ ACP Client    ├─ 商业化基础设施
   └─ 单用户        └─ 多端准备      └─ 安全审计      └─ 企业功能
```

## V0.1 Alpha（MVP，10 周）

**目标**：单人创作者的本地优先内容工作台，跑通"导入 → 分析 → 脚本 → 渲染 → 导出"最小闭环。

- ✅ 桌面应用（Tauri + React）
- ✅ 本地素材导入与 ASR
- ✅ AI 快速分析与脚本生成
- ✅ 单模板视频草稿渲染
- ✅ CLI 稳定版
- ✅ MCP Server 技术演示（5 个只读 Tool）
- ⏸ Publisher Engine：仅审计与接口设计
- ⏸ 移动端、协作、插件市场：不在范围

**验收**：10 名种子用户 ≥7 完成完整流程，7 日复用 ≥4。详见 [PRD.md](./docs/PRD.md)。

## V0.2 Beta

- 插件 SDK（Python + TypeScript）
- 多 Workspace 支持
- 主题系统（浅色 / 深色 / 高对比）
- 协作基础（只读分享、评论）
- 品牌配置文件（BrandProfile）完整版

## V0.5 RC

- 插件市场（registry）
- Publisher Engine 正式发布（Douyin / 视频号 / Bilibili）
- A2A Server
- ACP Client
- 第三方安全审计

## V1.0 GA

- 完整 Agent 互操作（CLI / MCP / A2A / ACP）
- 完整 Provenance UI（AI 标签、来源追溯、可解释性）
- 插件签名与权限审计
- 商业化基础设施（订阅、激活、企业部署）
- 移动端只读伴侣 App（可选）

## 周期估算

依据 STRATEGY_PLAN §1.2 决策，**单人资源按 10-12 个月规划 V1.0**：

| 阶段 | 周期 |
|---|---|
| V0.1 Alpha | 8-10 周 |
| V0.2 Beta | +6-8 周 |
| V0.5 RC | +12-16 周 |
| V1.0 GA | +12-16 周 |
| **累计** | **38-50 周（约 10-12 个月）** |

## 范围控制

每周五对照 [STRATEGY_PLAN.md](./docs/STRATEGY_PLAN.md) 检查范围蔓延。任何新增需求必须替换掉同等 Effort 的现有项。

## 决策记录

所有架构决策见 [docs/DECISIONS.md](./docs/DECISIONS.md) 与 [docs/adr/](./docs/adr/)。
