# ADR-004: Command Bus 与 Application Services 模式

- **Status**: Accepted
- **Date**: 2026-07-21
- **Deciders**: @ra1nzzz

## Context

STEPWORK 需要统一处理来自多个入口的请求：

- UI（Tauri WebView）
- CLI（`stepwork` 命令行）
- MCP Server（供 Claude 等 AI 客户端调用）
- A2A Server（供其他 Agent 调用）
- ACP（本地 Agent 子进程）
- Plugin（第三方扩展）
- Scheduled（定时任务）

所有入口必须执行相同的**业务规则、权限检查、审计**，避免逻辑分散。

## Decision

采用 **Command Bus + Application Services** 模式，**宿主在 Python Worker**（参见 D5）：

1. 所有请求封装为 **Command Envelope**（见 `schemas/command-envelope.schema.json`）
2. 协议 Adapter（CLI/MCP/A2A/ACP/UI）仅做格式转换，**不得直接调用数据库**
3. Command Bus 统一执行：Schema 验证 → Actor/Scope 解析 → 幂等检查 → Application Service 调度 → 审计记录
4. Application Services 按领域划分：Project / Content / Analysis / Agent / Render / Publish / Plugin / Approval

## Consequences

**正面**：
- 业务规则单一事实源，7 个入口不可能越权
- 幂等键统一处理，避免重复执行
- 高风险 Command 可统一返回 `ApprovalRequired`
- 审计事件自然落库

**负面**：
- 单次调用延迟增加 ~1ms（Schema 验证 + 审计写入）
- 所有 Command 必须先定义 Schema，前期投入较大

**关联**：SYSTEM_SPEC §7 完整定义；D5 决定宿主在 Python Worker。
