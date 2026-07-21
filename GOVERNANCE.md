# STEPWORK Governance

## 当前模型：BDFL（Benevolent Dictator For Life）

STEPWORK 在 V1.0 之前采用 **BDFL（仁慈独裁者）** 治理模型，由单一维护者 @ra1nzzz 拥有最终决策权。

**理由**：项目处于架构定型与快速迭代期，需要果断决策以避免范围蔓延（参见 STRATEGY_PLAN §2.1）。多人委员会制会显著拖慢 MVP 节奏。

## 维护者职责

| 职责 | 说明 |
|---|---|
| 架构决策 | 批准 / 否决 ADR，维护 SYSTEM_SPEC 一致性 |
| 代码审查 | 所有 PR 的最终 approve 权限 |
| 发布管理 | 版本号、Release Notes、签名密钥 |
| 范围控制 | 每周对照 PHASE_PLAN 检查范围蔓延 |
| 安全响应 | 接收并处置 SECURITY.md 中的漏洞报告 |
| 许可证合规 | 维护 LICENSE_AUDIT 与 THIRD_PARTY_NOTICES |

## 决策流程

### 常规变更（代码、文档、小型重构）

1. 提交 PR → CI 通过 → 维护者 review → squash merge
2. 无需前置讨论，但鼓励在 Issue 中先讨论大改动

### 架构决策（影响多个模块、长期方向）

1. **提案**：在 `docs/adr/` 新建 `ADR-NNN-<slug>.md`，状态置为 `Proposed`
2. **讨论**：通过 GitHub Issue / Discussion 公开讨论 ≥ 7 天
3. **裁决**：BDFL 在讨论结束后 7 天内做出 Accept / Reject / Supersede 决定
4. **记录**：更新 ADR Status 并在 `docs/DECISIONS.md` 中引用

### 紧急决策（安全漏洞、生产事故）

- BDFL 可绕过讨论直接决策，事后 48 小时内补写 ADR 并公开理由

## RFC 流程（V1.0 后启用）

V1.0 发布后，治理模型将演进为 **BDFL + RFC 委员会**：

1. 任何人均可提交 RFC（格式同 ADR，但影响力更大）
2. 设立 3-5 人 RFC 委员会（核心贡献者 + 领域专家）
3. 重大决策需 RFC 委员会 ≥ 2/3 多数通过，BDFL 保留否决权
4. 维护者退位机制：连续 6 个月无活动可被委员会启动替换流程

详细 RFC 模板与委员会选举规则将在 V1.0 发布前通过 RFC-001 确定。

## 贡献者晋升路径

| 角色 | 权限 | 准入标准 |
|---|---|---|
| Contributor | 提交 PR | 首次 PR 合入 |
| Reviewer | Review + Comment | ≥ 5 个高质量 PR，维护者邀请 |
| Committer | Push 到非保护分支 | ≥ 3 个月活跃 + ≥ 10 个 PR + 维护者提名 |
| Maintainer | 合并 PR、发布 | ≥ 6 个月活跃 + 深度参与架构 + BDFL 提名 |

## 商标与品牌

"STEPWORK" 名称与 Logo 由 BDFL 保留商标权，使用规范见 [TRADEMARK.md](./TRADEMARK.md)。

## 商业使用

STEPWORK 核心代码以 AGPL-3.0-or-later 发布，允许商业使用但要求衍生作品开源。BDFL 保留未来提供商业双重许可的权利（与所有贡献者协商后）。

## 修订

本文档的修订本身即属于架构决策，需通过 ADR 流程。
