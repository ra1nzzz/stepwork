# ADR-006: 双许可证策略（AGPL Core + Apache SDK）

- **Status**: Accepted
- **Date**: 2026-07-21
- **Deciders**: @ra1nzzz

## Context

STEPWORK 需要在开源与商业化之间取得平衡：

- **太宽松**（MIT/Apache 全量）：商业公司可闭源改造，社区得不到回馈
- **太严格**（GPL/AGPL 全量）：SDK/插件生态受抑制，企业不敢用
- **Open Core**（核心闭源）：违背"本地优先 + 用户拥有数据"的价值观

## Decision

采用**双许可证**（见 [LICENSE](../../LICENSE)）：

- **AGPL-3.0-or-later**：Core / Worker / Desktop / Agent-Interop / Publisher Engine
- **Apache-2.0**：Python SDK / TypeScript SDK / Plugin SDK / Agent Adapter SDK / 示例插件
- **CC BY-SA 4.0**：文档
- **CC0-1.0**：JSON Schema（供任意实现自由使用）

**商标保留**："STEPWORK" 名称与 Logo 由维护者保留（见 TRADEMARK.md）。

## Consequences

**正面**：
- 核心代码强制回馈（AGPL 网络条款覆盖 SaaS 场景）
- SDK/插件生态友好（Apache-2.0 允许商业闭源插件）
- 商标保护防止山寨 fork 混淆
- 未来可提供商业双重许可（与所有贡献者协商）

**负面**：
- 大型企业可能因 AGPL 放弃使用（可接受：目标用户是个人创作者与小团队）
- 需要 CLA 或 DCO 确保未来可双重许可（当前采用 DCO，见 CONTRIBUTING.md）

**关联**：LICENSE 文件已落实；GOVERNANCE.md 中 BDFL 保留双重许可权利。
