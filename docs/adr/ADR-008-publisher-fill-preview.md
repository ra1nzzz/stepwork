# ADR-008: Publisher 默认 Fill and Preview，禁止无确认发布

- **Status**: Accepted
- **Date**: 2026-07-21
- **Deciders**: @ra1nzzz

## Context

STEPWORK 的 Publisher Engine 通过浏览器自动化在内容平台（抖音、视频号、B 站等）发布内容。需要决定自动化程度。候选：

- **OPEN_ONLY**：仅打开浏览器，让用户手动操作
- **FILL_AND_PREVIEW**：自动填写表单 + 上传素材，停在预览页，用户手动点发布
- **CONFIRMED_PUBLISH**：自动填写 + 自动点击发布（需用户预先确认）
- **POLICY_CONTROLLED_AUTO**：基于策略完全自动发布（无需逐次确认）

风险评估：自动发布一旦被滥用（水军、批量垃圾内容），平台会封号，用户会失去对 STEPWORK 的信任。

## Decision

**V0.1-V0.5 默认且唯一支持 FILL_AND_PREVIEW**：

- Publisher Engine 自动完成：打开浏览器 → 登录（凭据来自系统密钥链）→ 填写标题/正文/标签 → 上传封面/视频 → 停在预览页
- **最终点击"发布"按钮必须由用户手动完成**
- CONFIRMED_PUBLISH 仅在 V1.0 后通过显式 Approval 启用，且需要：
  - 每次发布前展示完整预览
  - 用户输入独立确认码（非常规勾选）
  - 绑定 content_hash 防重放

**显式禁止**（SYSTEM_SPEC §14.6）：

- Canvas/WebGL 指纹伪装
- 隐藏 WebDriver 的反检测脚本
- 多账号轮换规避风控
- 无确认最终发布
- 默认 `--no-sandbox`

## Consequences

**正面**：
- 用户始终保有最终决策权，符合 Human-in-the-loop 原则（SYSTEM_SPEC §2 第 5 条）
- 降低被平台判定为"批量操作"的风险
- 法律责任清晰（用户是发布主体）

**负面**：
- 无法完全自动化，用户需要逐次确认（但这就是设计意图）
- 相比 media-auto-pilot 等"全自动"工具，能力显得弱

**关联**：SYSTEM_SPEC §14 完整定义；ADR-010 关联 media-auto-pilot 迁移范围。
