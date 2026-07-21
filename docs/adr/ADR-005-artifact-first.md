# ADR-005: Artifact-first 数据模型

- **Status**: Accepted
- **Date**: 2026-07-21
- **Deciders**: @ra1nzzz

## Context

STEPWORK 的核心差异化是**可解释性与来源追溯**。用户需要回答"这段内容是哪个模型/Agent 在什么 Prompt 下生成的？"。

传统做法：在文档里嵌入元数据字段（如 `doc.model = "gpt-4"`）。问题：

- 多源混编时无法精确追溯段落级来源
- 用户手动编辑后 AI 标签失效
- Agent 产出的中间结果无法独立审计

## Decision

采用 **Artifact-first 数据模型**（SYSTEM_SPEC §11）：

- 任何**离散产出物**都是 Artifact（转写稿、分析报告、选题提案、脚本草稿、渲染视频等）
- 每个 Artifact 携带：producer（user/model/agent/plugin/system）、contentHash、sourceRefs、trustLevel
- Artifact 通过 Envelope 标准化（见 `schemas/artifact-envelope.schema.json`）
- ContentVersion 由 Artifact 合并而来，保留来源链
- 外部 Agent 产出默认 `PENDING_REVIEW`，用户显式接受后才成为正式 Artifact

## Consequences

**正面**：
- 段落级来源追溯（ProvenanceRecord 关联 Artifact）
- AI 标签合规（EU AI Act 等法规要求）
- Agent 协作可审计
- 数据可导出、可迁移

**负面**：
- 数据模型复杂度上升（Artifact + ContentVersion + ProvenanceRecord 三表）
- 存储开销增加（每个 Artifact 存 hash + producer + refs）
- UI 需要展示来源链，开发量大

**关联**：SYSTEM_SPEC §11 完整定义；D1 任务状态机与 Artifact 流转关联。
