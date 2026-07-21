# STEPWORK 最优策略计划（Strategy Plan）

**版本：V1.0**
**日期：2026-07-21**
**定位：基于现有文档评审的迭代优先级与执行策略**
**上位文档：PRODUCT_CHARTER.md、PRD.md、MVP_PLAN.md、PHASE_PLAN.md、SYSTEM_SPEC.md**

---

## 1. 文档评审结论

### 1.1 文档健康度

| 文档 | 状态 | 主要问题 |
|---|---|---|
| PRODUCT_CHARTER.md | 完整、成熟 | 版本路线图与 PHASE_PLAN 存在轻微周期差异 |
| PRD.md | 完整、可执行 | 与 MVP_PLAN 的 P0/P1 映射基本清晰 |
| SYSTEM_SPEC.md | 完整、技术基线 | 任务状态机与 CHARTER 不一致，目录结构两版本并存 |
| MVP_PLAN.md | 完整、10 周可落地 | 工作流分解较粗，缺少明确资源/角色分配 |
| PHASE_PLAN.md | 完整、阶段清晰 | 与 CHARTER 的 V0.1-V1.0 周期估算存在偏差 |
| Prototype/ | 设计保真度高 | 仅桌面深色、min-width 1120px，与 manifest 响应式承诺不符 |

### 1.2 关键不一致（必须修复）

1. **任务状态机冲突**
   - CHARTER 第 8.4 节列出 15 个细粒度状态（PENDING→COMPLETED 等）
   - SYSTEM_SPEC 第 10.1 节定义为 9 个主状态 + 9 个业务阶段
   - **决策**：以 SYSTEM_SPEC 为准，CHARTER 作为产品叙事，开发前冻结状态枚举

2. **本地目录结构两版本**
   - CHARTER：`STEPWORK_HOME/projects/<project-id>/`
   - SYSTEM_SPEC：`STEPWORK_HOME/workspaces/<workspace-id>/projects/<project-id>/`
   - **决策**：采用 SYSTEM_SPEC 的 workspace 嵌套结构，为 V0.4 多 Workspace 预留

3. **周期估算不一致**
   - CHARTER：V0.1 约 4-6 周，V1.0 累计 5-8 个月
   - PHASE_PLAN：V0.1 约 8 周，V1.0 累计 9-12 个月（单人）或 6-8 个月（团队）
   - **决策**：以 PHASE_PLAN 为准，单人资源按 10-12 个月规划

4. **Prototype 响应式缺口**
   - DESIGN-MANIFEST 要求覆盖 360-1920 全视口矩阵
   - 实际 CSS：`body { min-width: 1120px }`，无移动端适配
   - **决策**：MVP 阶段明确“桌面优先”，在 PRD 中声明移动端不在 V0.1 范围；Prototype 仅作桌面视觉基线

---

## 2. 核心策略判断

### 2.1 最高风险：范围蔓延

STEPWORK 的文档体系完整到“可以写一年”的程度。当前 10 周 MVP 计划已做裁剪，但仍有隐性膨胀：

- CLI + MCP Server 同时进 MVP，机器入口工作量被低估
- Publisher Engine 虽标为“评估”，但审计+接口设计+PoC 会消耗 1-2 周
- BrandProfile 基础版、插件 Manifest、Provenance 页面并列在 Week 8，存在排期冲突

**策略**：再砍一刀，把 MVP 定义为“创作闭环 + CLI”，MCP Server 降为“技术演示”。

### 2.2 最大杠杆：Artifact-first

文档反复强调 Artifact 与 Provenance，这是差异化核心。但 MVP_PLAN 中 Artifact 页面直到 Week 8 才出现，且没有明确验收标准。

**策略**：Artifact 模型前置到 Week 2（与 Command Bus 同时冻结），但 UI 呈现保持极简（仅项目页内嵌列表），不做独立“最近产物”面板。

### 2.3 最被低估：任务引擎稳定性

所有长任务（ASR、分析、渲染）都依赖 SQLite Job 表 + Lease + 恢复机制。Week 2 的 Gate 是“强制关闭后无数据丢失”，但测试矩阵中“应用异常退出”直到 Week 9 才出现。

**策略**：任务引擎测试前置，Week 2 开始每晚自动跑“kill -9 恢复”测试；Week 6 渲染任务必须接入同一套 Lease 机制。

---

## 3. 优化后的 10 周执行计划

### 3.1 关键路径（不可并行，延迟即延期）

```text
W1 骨架 → W2 数据底座 → W3-W4 素材与 ASR → W5 脚本 → W6 渲染 → W9 RC1
```

### 3.2 并行工作流（资源允许时插入）

| 工作流 | 内容 | 前置条件 | 最晚完成 |
|---|---|---|---|
| Agent Interop | CLI 骨架 → CLI 全命令 → MCP 技术演示 | W2 Command Bus | W8 |
| Publisher Migration | 审计 → 新接口设计 → Fill/Preview PoC | W1 仓库就绪 | W8 |
| 品牌与合规 | BrandProfile 基础字段 → Provenance 页面 | W5 ContentVersion | W9 |
| 社区与发布 | 开源仓库 → 种子招募 → Onboarding | 持续 | W10 |

### 3.3 逐周计划

#### Week 1：Monorepo 与桌面骨架
- Tauri + React + Python Sidecar 启动/健康检查/退出
- CI（Rust/TS/Python）+ Windows 安装包
- `media-auto-pilot` 审计（标记保留/重写/删除，隔离反检测代码）
- 冻结目录结构：`STEPWORK_HOME/workspaces/<id>/projects/<id>/`
- **Gate**：Sidecar 可管理，安装包可启动

#### Week 2：领域模型、Command Bus 与任务引擎
- SQLite migrations（Workspace/Project/SourceAsset/Job）
- Command Envelope v1 + Application Gateway
- Job 表 + Lease + 取消/重试/恢复
- Tauri Events 任务状态推送
- Artifact Envelope v1（模型冻结，UI 后置）
- **Gate**：kill -9 后 Job 与项目数据 100% 恢复

#### Week 3：素材导入与 ASR
- 拖放导入、文件哈希、媒体元数据
- ASR Provider 接口 + 一个实现（本地或云）
- Transcript UI（时间戳、错误、重试）
- **Gate**：20 个测试视频 ≥18 个完成转写

#### Week 4：AI Provider 与内容分析
- AI Provider SDK 雏形
- 一个云 Provider + 一个 OpenAI Compatible/Ollama Provider
- 分析 Prompt + JSON Schema + AnalysisReport
- 费用/模型/数据上传透明提示
- **Gate**：30 个样本 ≥90% Schema 合法，失败可切换 Provider

#### Week 5：原创角度与脚本编辑器
- TopicProposal（3-5 个差异化角度）
- 脚本生成 + TipTap/Lexical 编辑器
- ContentVersion + 自动保存 + 版本比较
- **Gate**：刷新/重启不丢稿，版本链完整

#### Week 6：视频草稿渲染
- Renderer 插件接口雏形
- 一个 9:16 字幕/背景模板
- TTS Provider + 用户录音输入
- FFmpeg 渲染 + RenderJob 进度/取消/重试
- **Gate**：10 个连续渲染无崩溃，取消后 0 僵尸进程

#### Week 7：CLI 稳定版
- `stepwork` CLI 全 P0 命令
- JSON 输出、退出码、Job ID、幂等键
- CLI ↔ Application Gateway 完整接入
- **Gate**：CLI 与 UI 对同一 Command 结果一致

#### Week 8：MCP 技术演示 + 插件 Manifest
- MCP Server stdio（仅 Tools：list_projects/get_project/import_source/analyze_source/get_job_status）
- 插件 Manifest v1 + 加载/启停/权限展示
- 一个示例 AI Provider 插件
- BrandProfile 基础字段（定位/受众/语气/禁用表达）
- **Gate**：MCP 无法越权，禁用插件后 Core 稳定

#### Week 9：集成、数据迁移与种子测试
- 全链路 E2E（导入→分析→脚本→渲染→导出）
- 数据备份/恢复、项目导出/导入
- 安装/升级/卸载、日志脱敏
- 5 名种子用户测试，修复 P0/P1
- **Gate**：无数据丢失，P0 流程完成率 ≥80%

#### Week 10：公开 Alpha 准备
- 10 名种子用户、Onboarding、示例项目
- 错误恢复文案、隐私与许可证、安全披露流程
- 发布包签名、Alpha Release Notes
- **Gate**：满足 PRD 验收指标或有明确偏差说明

### 3.4 被移出关键路径的项目

| 原位置 | 内容 | 新安排 | 理由 |
|---|---|---|---|
| W7 | MCP Server 完整版 | 降为技术演示，仅 5 个只读/低风险 Tools | 降低范围，CLI 已验证机器入口价值 |
| W8 | Provenance 独立页面 | 合并到项目 Overview 侧边栏 | 减少 UI 工作量，保留数据可见性 |
| W8 | AgentTask/AgentArtifact UI | 仅数据模型 + 数据库表，UI 占位 | A2A/ACP 后置，不阻塞 MVP |
| W2 | 安全凭据存储 PoC | 与 Week 1 密钥链集成合并 | 提前发现 Tauri Stronghold 限制 |

---

## 4. 优先级重排（RICE 简化版）

| 功能 | Reach | Impact | Confidence | Effort | Score | 决策 |
|---|---:|---:|---:|---:|---:|---|
| 任务引擎恢复 | 全部用户 | 3.0 | 90% | 1.5 | 高 | P0，前置测试 |
| 本地素材导入 | 全部用户 | 3.0 | 95% | 1.0 | 高 | P0 |
| ASR 转写 | 全部用户 | 2.5 | 85% | 1.5 | 高 | P0 |
| 快速分析 | 全部用户 | 3.0 | 80% | 2.0 | 高 | P0 |
| 脚本编辑+版本 | 全部用户 | 3.0 | 90% | 2.0 | 高 | P0 |
| 视频草稿（单模板） | 60% 用户 | 2.5 | 70% | 2.5 | 中高 | P0，但允许标记实验 |
| CLI | 20% 用户 | 2.5 | 90% | 1.5 | 中高 | P0 |
| MCP Server | 10% 用户 | 2.0 | 60% | 2.0 | 中 | 技术演示，仅 5 Tools |
| BrandProfile 基础 | 40% 用户 | 2.0 | 70% | 1.0 | 中 | P1，有余力加入 |
| 插件 Manifest | 15% 用户 | 2.0 | 80% | 1.5 | 中 | P1 |
| Publisher Engine 审计 | 30% 用户 | 2.5 | 60% | 2.0 | 中 | 仅审计+接口设计，PoC 可选 |
| A2A/ACP 数据模型 | 5% 用户 | 2.0 | 50% | 2.0 | 低 | 仅表结构，不实现协议 |

---

## 5. 风险登记册（Top 6）

| 风险 | 概率 | 影响 | 应对 | 负责人 |
|---|---|---|---|---|
| Tauri + Python Sidecar 打包不稳定 | 中 | 高 | W1 即构建安装包；备选 Electron 或 Rust Worker | 架构 |
| 模型输出不稳定，Schema 不达标 | 高 | 高 | Schema Validation + Repair + Provider 切换；失败可编辑 | AI |
| 范围蔓延，10 周做不完 | 高 | 高 | 本计划已再砍一刀；每周五范围冻结 | 产品 |
| 任务引擎恢复测试不充分 | 中 | 高 | W2 起每晚自动 kill 测试；渲染任务 W6 接入 Lease | 工程 |
| 种子用户只尝鲜不复用 | 中 | 中 | 7 日内安排第二次真实任务；访谈聚焦触发条件 | 产品 |
| Publisher 审计发现重大许可证/架构问题 | 低 | 高 | W1 完成审计；备选全新实现 | 架构 |

---

## 6. 发布决策矩阵

### GO
- P0 功能全部可用，无数据丢失
- CLI 稳定，MCP 技术演示可运行
- 10 名用户 ≥7 完成流程，7 日复用 ≥4
- 无高危安全问题，安装包在目标 Windows 通过

### CONDITIONAL GO
- 视频草稿质量一般，但脚本和 Artifact 价值明确
- MCP 仅 3 个 Tools 稳定，其余标记 Beta
- 将 Renderer 标记为实验，继续发布 Alpha

### NO-GO
- 项目数据丢失或 Job 无法恢复
- CLI/MCP 可越权访问未授权项目
- 分析结果不能稳定落入 Schema
- 用户无法理解项目与版本模型

---

## 7. 下一步行动（Next Actions）

1. **今天**：修复文档不一致（状态机、目录结构、周期估算），在 `docs/` 根目录添加 `DECISIONS.md` 记录上述 4 项决策
2. **本周**：按 Week 1 计划启动 Monorepo，完成 `media-auto-pilot` 审计
3. **持续**：每周五对照本计划检查范围，任何新增需求必须替换掉同等 Effort 的现有项
4. **W9**：根据种子用户反馈，决定 V0.2 优先做插件 SDK 还是 Publisher Engine

---

## 8. 附录：Prototype 使用指南

- `Prototype/index.html` 为 launcher/overview，仅作导航入口
- 各屏幕文件（`projects.html`、`inspector.html`、`studio.html`、`render.html`、`tasks.html`、`settings.html`）应映射为独立路由
- `styles.css` 为视觉基线，需提取 design tokens（背景/表面/前景/边框/强调/半径/字体）
- 当前实现仅覆盖桌面深色模式，移动端与浅色模式不在 V0.1 范围
- `app.js` 中的 toast、tab、modal 行为需保留，并补充 loading/empty/error/success 状态
- 实现前以 `DESIGN-MANIFEST.json` 为机器可读地图，逐屏核对 tokens、交互与响应式断点
