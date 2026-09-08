# STEPWORK 阶段发展计划（PHASE PLAN）

**版本：V1.0**  
**规划区间：Phase 0—V1.0 及后续**  
**基准资源：1 名核心负责人 + AI Coding Agents；小团队可并行压缩周期**

---

## 1. 总体路线

```text
Phase 0  架构与风险验证
   ↓
V0.1     本地创作闭环 + CLI/MCP
   ↓
V0.2     插件 SDK + Publisher Engine + MCP Client
   ↓
V0.3     A2A/ACP + Agent 协作 + 确认后发布
   ↓
V0.4     团队、企业与私有化
   ↓
V1.0     稳定开放平台
```

计划原则：

- 每个阶段必须有独立用户价值；
- 后续阶段不能成为前一阶段发布的必要条件；
- 协议实现可以后置，但内部模型和安全边界必须前置；
- 不以功能数量作为阶段完成标准；
- 每阶段结束进行继续、收缩或转向决策。

---

## 2. 时间假设

### 单人 + AI Coding Agents

- Phase 0 + V0.1：10 周；
- V0.2：6—8 周；
- V0.3：8—10 周；
- V0.4：8—12 周；
- V1.0 稳定化：6—8 周；
- 总计：约 9—12 个月。

### 3—5 人小团队

- Phase 0 + V0.1：8—10 周；
- V0.2：4—6 周；
- V0.3：6—8 周；
- V0.4：6—8 周；
- V1.0 稳定化：4—6 周；
- 总计：约 6—8 个月。

---

## 3. Phase 0：架构与风险验证

**周期：2 周**

### 3.1 目标

- 证明桌面壳、Sidecar、任务恢复和本地存储可行；
- 完成 `media-auto-pilot` 迁移与许可证评估；
- 冻结第一版内部 Command、Job 和 Artifact Schema；
- 避免在后续阶段分别为 UI、CLI 和 Agent 协议重写业务。

### 3.2 交付

- Tauri + React + Worker 骨架；
- SQLite migrations；
- Command Bus；
- Job Engine PoC；
- Artifact Envelope；
- Windows 安装包；
- `MIGRATION_ASSESSMENT.md`；
- `LICENSE_AUDIT.md`；
- ADR-001—010。

### 3.3 Gate

- Sidecar 可管理；
- 任务重启恢复；
- 数据不丢失；
- Windows 打包可行；
- 迁移代码权利和删除范围清晰。

### 3.4 停止条件

若 Tauri + Python Sidecar 在目标环境无法稳定打包，应在本阶段切换 Electron 或 Rust 原生 Worker，不进入 V0.1 后再调整。

---

## 4. V0.1：本地内容创作闭环

**周期：8 周**

### 4.1 用户价值

用户能在本地完成：

```text
导入 → 转写 → 分析 → 原创角度 → 脚本 → 视频草稿 → 导出
```

外部 Agent 可通过 CLI/MCP 调用核心能力。

### 4.2 核心能力

- 项目和素材；
- AI/ASR Provider；
- AnalysisReport；
- TopicProposal；
- ContentVersion；
- 一个 Renderer；
- Artifact/Provenance；
- CLI；
- MCP Server；
- 插件 Manifest 雏形。

### 4.3 社区目标

- 开源仓库和基础文档；
- 10 名种子用户；
- 5 个有效外部 Issue；
- 1 个外部贡献或插件实验。

### 4.4 商业/可持续目标

本阶段不追求盈利，验证：

- 是否有企业愿意讨论私有部署；
- 是否有人愿意赞助；
- 哪类插件最有定制价值。

### 4.5 Gate

- 7 日复用信号；
- 核心数据安全；
- CLI/MCP 使用价值；
- 明确下一阶段优先级是插件、Publisher 还是 Agent 协作。

---

## 5. V0.2：插件化与发布执行基础

**周期：6—8 周**

### 5.1 用户价值

- 用户可以替换模型、语音和渲染能力；
- 开发者可以编写插件；
- 用户可通过本地浏览器完成一个平台的填写和预览；
- STEPWORK 可以调用外部 MCP Server。

### 5.2 功能

#### Plugin Runtime

- Manifest v1；
- 权限和 Scope；
- 独立进程；
- 健康检查；
- 安装、禁用、升级和卸载；
- Official/Verified/Community/Experimental。

#### SDK

- Source Provider SDK；
- AI Provider SDK；
- Speech Provider SDK；
- Renderer SDK；
- Publisher SDK；
- Agent Adapter SDK。

#### Publisher Engine

- Media Auto Pilot 代码抽取；
- 跨平台 Browser Discovery；
- Profile 隔离；
- CDP Session；
- DOM Snapshot；
- Uploader；
- 抖音 Fill and Preview 插件；
- 发布前证据。

#### Agent

- MCP Client；
- 外部 MCP Server 管理；
- Artifact 信任等级；
- Agent Connections 基础 UI。

### 5.3 社区目标

- 3 个官方插件；
- 2 个社区插件；
- 插件开发模板；
- Conformance Tests。

### 5.4 收入验证

- 接受平台插件或企业 Provider 定制；
- 争取第一个付费定制项目；
- 推出企业赞助说明。

### 5.5 Gate

- 第三方插件不会破坏 Core；
- 权限模型可理解；
- Publisher 填写成功率达到内部标准；
- 用户对本地浏览器辅助有实际需求。

---

## 6. V0.3：Agent 协作与安全发布

**周期：8—10 周**

### 6.1 用户价值

- STEPWORK 可作为内容专业 Agent 与其他 Agent 协作；
- 用户可在桌面中连接交互式 Agent；
- Agent 任务和 Artifact 有统一 Inbox；
- 发布可在明确确认后完成。

### 6.2 A2A

- Agent Card；
- Skills；
- A2A Server；
- A2A Client；
- Task/Artifact 映射；
- 流式状态；
- 超时和取消；
- 外部 Agent 委派示例。

### 6.3 ACP

- ACP Client；
- 本地 Agent 子进程；
- Session；
- 流式更新；
- 权限请求；
- 文件差异；
- MCP Bridge；
- 插件开发/诊断场景。

### 6.4 Agent UX

- Agent Connections；
- Agent Task Inbox；
- Artifact Inbox；
- Approval Center；
- Session Trace。

### 6.5 发布

- PublishApproval；
- 一次性 Token；
- Confirmed Publish；
- 发布结果验证；
- 证据和审计；
- 插件过期提示。

### 6.6 社区目标

- 2—5 个 Agent Adapter；
- 1 个完整多 Agent 示例工作流；
- Agent Interop 文档和示例项目。

### 6.7 收入目标

- Agent/企业系统接入定制；
- Publisher 插件定制；
- 赞助者 Preview Build。

### 6.8 Gate

- Agent 不可绕过审批；
- Artifact 采用率有真实信号；
- A2A/ACP 不造成 Core 业务重复；
- 发布重复事故为 0。

---

## 7. V0.4：团队与企业能力

**周期：8—12 周**

### 7.1 用户价值

- 小团队可以共享项目、审核和 Agent 工作流；
- 企业可以私有部署和接入内部系统；
- 数据和插件策略可集中管理。

### 7.2 功能

- 多 Workspace；
- 成员和角色；
- 审批流程；
- 团队项目同步；
- 企业知识库；
- 企业内部 Agent Gateway；
- 私有插件仓库；
- 统一配置分发；
- 审计导出；
- 备份与恢复；
- SSO 作为企业定制或插件。

### 7.3 部署形态

- 完全本地单机；
- 局域网协作；
- 企业私有服务；
- 可选官方云同步。

### 7.4 收入目标

- 2—3 个企业试点；
- 私有部署、培训和支持合同；
- 行业模板与连接器定制。

### 7.5 Gate

- 多用户数据隔离；
- 审计完整；
- 企业部署升级可维护；
- 不破坏本地单机免费核心。

---

## 8. V1.0：稳定开放平台

**周期：6—8 周稳定化**

### 8.1 发布标准

- Windows 和 macOS 正式版；
- Linux 社区版或实验版；
- 数据迁移策略稳定；
- 插件 API v1 稳定；
- CLI/MCP 稳定；
- A2A/ACP 基础能力可用；
- Publisher 安全模型稳定；
- 安装、更新和回滚流程完善；
- 安全文档和漏洞响应；
- 许可证与 SBOM 完整。

### 8.2 生态目标

- 5—10 个官方插件；
- 10 个以上社区插件或 Adapter；
- 20—50 名稳定活跃用户；
- 5 名以上外部贡献者；
- 3 个企业或行业案例。

### 8.3 可持续目标

- 稳定月度赞助；
- 定制项目收入覆盖主要维护成本；
- 官方服务处于可选 Beta；
- 至少 2 名核心维护者能够发布版本。

---

## 9. V1.0 之后候选方向

不提前承诺，根据数据选择：

### 内容智能

- 用户偏好学习；
- 多来源事实核查；
- 评论和数据回流；
- 内容表现复盘；
- 跨项目知识图谱。

### Agent 平台

- Agent Registry；
- 分布式任务；
- 跨设备 Agent；
- 策略引擎；
- 企业 Agent Gateway。

### 创作能力

- 更多 Renderer；
- 第三方剪辑工程导出；
- 直播切片；
- 图像和视频生成插件；
- 受控声音克隆。

### 平台生态

- 更多 Publisher；
- 官方 API 插件；
- 数据回流插件；
- 合规和行业插件。

---

## 10. 跨阶段依赖

| 能力 | Phase 0 | V0.1 | V0.2 | V0.3 | V0.4 |
|---|---:|---:|---:|---:|---:|
| Command Bus | 设计/PoC | 稳定 | 扩展 | 稳定 | 企业策略 |
| Artifact | Schema | 内容 Artifact | 插件 Artifact | Agent Artifact 完整 | 团队共享 |
| CLI | 骨架 | 稳定 | 扩展 | 稳定 | 管理命令 |
| MCP | 模型预留 | Server | Client | Agent Bridge | Enterprise Gateway |
| A2A | 映射预留 | 无 | PoC | 完整基础 | 企业策略 |
| ACP | Session 预留 | 无 | PoC | Client 基础 | 远程/管理 |
| Plugin | Manifest 预留 | Provider 内置 | Runtime/SDK | Adapter 生态 | 私有仓库 |
| Publisher | 审计 | 无主路径 | Fill/Preview | Confirmed Publish | 审批/企业 |

---

## 11. 技术债务政策

每个阶段允许的技术债：

- V0.1 可只支持 Windows x64；
- V0.1 插件可先支持 Python；
- V0.1 MCP 只支持 stdio；
- V0.2 Publisher 只支持一个平台；
- V0.3 ACP 只支持本地子进程；
- V0.4 团队同步可先依赖私有服务。

不可接受的技术债：

- 绕过 Application Services；
- 明文凭据；
- 无 Schema 的 Agent/插件输出；
- 无幂等的发布；
- 无迁移的数据库修改；
- 业务状态只在内存中；
- 指纹伪装和规避风控；
- 安全修复仅向付费用户开放。

---

## 12. Roadmap 调整机制

每个版本结束依据以下证据调整：

1. 用户实际完成率；
2. 7 日与 30 日复用；
3. Artifact 采用率；
4. 插件和 Agent 使用率；
5. 失败与支持成本；
6. 企业定制需求；
7. 社区贡献；
8. 协议生态成熟度。

若某项能力使用率低，不因已写入总纲而继续投入。

---

## 13. 版本发布节奏

- Nightly：自动构建，不保证数据兼容；
- Preview：赞助者和贡献者提前测试；
- Alpha：功能验证，允许破坏性变化但必须迁移；
- Beta：主要 Schema 冻结；
- Stable：兼容承诺和安全维护。

建议：

- 每 2 周发布 Preview；
- 每 4—6 周发布一个 Alpha/Beta；
- 安全修复随时发布。

---

## 14. 最终阶段判断

STEPWORK 不以一次性完成“热点、分析、生成、发布、数据回流、全协议 Agent 平台”为目标。

正确演进顺序是：

1. 先证明本地内容工作台有真实价值；
2. 再证明插件能够降低模型和平台耦合；
3. 再证明 Agent 协作产生的 Artifact 被用户采用；
4. 再开放发布和企业能力；
5. 最后形成稳定的开放平台。

