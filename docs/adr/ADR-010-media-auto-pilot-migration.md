# ADR-010: media-auto-pilot 作为 Publisher 迁移基础而非 Core

- **Status**: Accepted
- **Date**: 2026-07-21
- **Deciders**: @ra1nzzz

## Context

项目前身 `media-auto-pilot` 已实现浏览器自动化发布抖音视频。需要决定它在 STEPWORK 中的位置。候选：

- **直接作为 Core**：快速但引入大量历史包袱（CLI 导向、Linux 云环境专用、含反检测代码）
- **作为迁移基础**：复用 CDP Session / DOM 定位 / 多策略上传思想，重写为 PublisherAdapter 接口
- **完全弃用重写**：最干净但浪费已有工作

## Decision

**media-auto-pilot 作为 Publisher Engine 的迁移基础**，**不进入 Core**：

- 代码归档到 `archive/media-auto-pilot/`（不进入主构建路径）
- 按 SYSTEM_SPEC §20 迁移矩阵逐项处理：
  - ChromeProcess / BrowserSession / DOMSnapshot / SmartLocator / FileUploader → 重构复用
  - BasePlatform → 替换为 PublisherAdapter
  - DouyinPlatform → 迁移为官方插件
  - FrequencyController / Stealth scripts → **删除**
- 迁移审计见 [docs/MIGRATION_ASSESSMENT.md](../MIGRATION_ASSESSMENT.md)

## Consequences

**正面**：
- 复用 CDP 持久连接、DOM 快照、智能定位等核心思想，节省 ~2 周工作量
- 物理隔离反检测代码，避免污染主仓库
- 新 PublisherAdapter 接口强类型，易测试

**负面**：
- 重写工作量仍存在（接口化 + 测试 + 文档）
- 需要仔细审计确保删除项不混入新代码

**关联**：SYSTEM_SPEC §14 与 §20 完整定义；ADR-008 决定了 Publisher 的安全边界。
