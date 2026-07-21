# ADR-007: Agent 互操作 MVP 范围（CLI/MCP 优先，A2A/ACP 数据模型前置）

- **Status**: Accepted
- **Date**: 2026-07-21
- **Deciders**: @ra1nzzz

## Context

STEPWORK 要支持 Agent 互操作（AI Agent 调用 STEPWORK，或 STEPWORK 调用其他 Agent）。可选协议：

- **CLI**：最基础，人 + Agent 都可用
- **MCP**（Model Context Protocol）：Anthropic 主导，Claude Desktop / Cursor 已支持
- **A2A**（Agent-to-Agent）：Google 主导，跨 Agent 协作
- **ACP**（Agent Client Protocol）：Zed 主导，编辑器场景

全部实现工作量过大（每项约 2 周），MVP 阶段必须取舍。

## Decision

**V0.1 范围**：

- ✅ **CLI 完整实现**：`stepwork` 全 P0 命令
- ✅ **MCP Server 技术演示**：仅 5 个只读 / 低风险 Tool（`list_projects / get_project / import_source / analyze_source / get_job_status`）
- ⏸ **A2A Server**：V0.5 RC 实现
- ⏸ **ACP Client**：V0.5 RC 实现

**数据模型前置**：AgentConnection / AgentTask / AgentArtifact / AgentSession / ApprovalRequest 的表结构在 W1 迁移中建立（`migrations/0003_agent_placeholder.sql`），但**不实现协议**。

## Consequences

**正面**：
- MVP 范围可控（CLI 已验证机器入口价值，MCP 演示社区价值）
- 数据模型前置避免 V0.5 时迁移
- A2A/ACP 协议细节在 V0.5 时再敲定，避免过早绑定

**负面**：
- 早期用户无法体验完整 Agent 协作
- MCP 演示仅 5 个 Tool，可能引发"功能不全"的反馈

**关联**：STRATEGY_PLAN §3.4 已将 MCP 完整版降为技术演示；SYSTEM_SPEC §13 定义了完整规范。
