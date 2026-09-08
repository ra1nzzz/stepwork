# STEPWORK MVP 实施计划（MVP PLAN）

**版本：V1.0**  
**目标版本：V0.1 Windows Alpha**  
**计划周期：10 周（Phase 0 两周 + MVP 八周）**  
**团队假设：1 名产品/架构负责人 + AI Coding Agents；或 2—3 人小团队**

---

## 1. MVP 目标

在 Windows 上交付一个可被真实创作者使用的本地桌面 Alpha，验证以下核心假设：

1. 用户愿意在 STEPWORK 中管理内容项目；
2. 参考内容分析能有效转化为原创脚本；
3. 一个固定视频模板足以验证“视频草稿”价值；
4. CLI 和 MCP 能让外部 Agent 使用 STEPWORK，而不需要完整 A2A/ACP；
5. 本地优先、BYOK 和 Artifact 模型不会显著增加普通用户使用门槛。

---

## 2. MVP 交付范围

### 2.1 P0

- Tauri + React Windows 桌面应用；
- Python Worker Sidecar；
- SQLite 数据库和任务表；
- Workspace、Project、SourceAsset；
- 本地视频、音频、文本导入；
- 一个链接 Source Provider；
- 一个云 AI Provider；
- 一个 OpenAI Compatible/Ollama Provider；
- ASR；
- 快速内容分析；
- 原创角度；
- 脚本编辑和 ContentVersion；
- 一个 9:16 视频模板；
- MP4、SRT、音频导出；
- Artifact 和 Provenance；
- 稳定 CLI；
- 基础 MCP Server；
- 凭据安全存储；
- 本地日志与诊断包；
- `media-auto-pilot` 迁移评估。

### 2.2 P1（有余力加入）

- BrandProfile 基础版；
- 项目导出/导入；
- 一个额外 Renderer 模板；
- MCP Resources；
- 插件 Manifest 和示例 AI Provider；
- Publisher Engine 骨架和抖音 Fill/Preview 技术演示。

### 2.3 不进入 MVP

- 最终自动发布；
- A2A Server/Client；
- ACP Client；
- MCP Client；
- 热榜；
- 团队成员权限；
- 云同步；
- 插件市场；
- 数据回流；
- 声音克隆；
- 多平台发布。

---

## 3. 组织方式

### 3.1 工作流

- 每周一个可运行增量；
- 主分支始终可构建；
- 所有功能先定义 Schema 和验收测试；
- AI Coding Agent 负责实现与单测，人类负责人负责边界、审查和验收；
- 不在 MVP 中引入未经使用场景证明的框架。

### 3.2 Definition of Ready

任务开始前必须具备：

- 用户价值；
- 输入输出；
- Schema；
- 权限范围；
- 验收标准；
- 失败状态；
- 是否需要迁移。

### 3.3 Definition of Done

- 代码合并；
- 单元测试；
- 关键路径 E2E；
- 错误文案；
- 日志脱敏；
- 文档更新；
- Windows 构建通过；
- 不新增未声明权限。

---

## 4. 十周时间表

## Week 1：仓库、桌面骨架与代码审计

**目标**

建立可以持续迭代的 Monorepo，并完成现有发布代码评估。

**任务**

- 创建目录结构；
- Tauri + React 启动；
- Python Sidecar Hello/Health；
- CI：Rust、TS、Python；
- 许可证文件和第三方清单框架；
- 审计 `media-auto-pilot`；
- 标记保留、重写和删除代码；
- 删除或隔离反检测实现；
- 建立 ADR。

**交付**

- Windows 可启动安装包；
- `MIGRATION_ASSESSMENT.md`；
- `LICENSE_AUDIT.md`；
- 基础 CI。

**Gate**

Tauri 能启动 Sidecar、获得健康状态并正常退出。

---

## Week 2：领域模型、Command Bus 与任务引擎

**任务**

- SQLite migrations；
- Workspace/Project/SourceAsset；
- Command Envelope；
- Application Gateway；
- Job 表、Lease、取消和重试；
- Tauri Events 显示任务状态；
- 安全凭据存储 PoC；
- Artifact Envelope v1。

**交付**

- 创建项目；
- 导入文件；
- 创建并执行模拟 Job；
- 重启恢复任务。

**Gate**

应用强制关闭后，Job 状态和项目数据无丢失。

---

## Week 3：素材导入与 ASR

**任务**

- 拖放和文件选择；
- 文件哈希；
- 媒体元数据；
- 临时目录；
- ASR Provider 接口；
- 一个本地或云 ASR 实现；
- Transcript UI；
- 时间戳和错误处理。

**交付**

本地视频可生成可编辑逐字稿。

**Gate**

20 个测试视频中至少 18 个完成转写，不因单个失败阻塞队列。

---

## Week 4：AI Provider 与内容分析

**任务**

- AI Provider SDK 雏形；
- STEPFUN 或其他云 Provider；
- OpenAI Compatible/Ollama Provider；
- 分析 Prompt 与 JSON Schema；
- AnalysisReport；
- 模型调用费用和数据上传提示；
- Provider 超时、重试和错误映射。

**交付**

导入视频后输出结构化分析报告。

**Gate**

30 个样本中 90% 输出符合 Schema；失败可以重试或切换 Provider。

---

## Week 5：原创角度与脚本编辑器

**任务**

- TopicProposal；
- 3—5 个原创角度；
- 脚本生成；
- TipTap/Lexical 编辑器；
- ContentVersion；
- 自动保存；
- 版本比较；
- 来源和 Producer 展示。

**交付**

用户从分析报告生成并修改脚本。

**Gate**

所有生成和编辑均产生可追溯版本；刷新和重启不丢数据。

---

## Week 6：视频草稿渲染

**任务**

- Renderer 插件接口雏形；
- 一个 9:16 字幕/背景模板；
- TTS Provider；
- 用户录音输入；
- 字幕分句和时间对齐；
- FFmpeg 渲染；
- RenderJob 进度、取消和重试；
- 导出 MP4/SRT/音频。

**交付**

脚本可生成可播放视频草稿。

**Gate**

10 个标准项目连续渲染无崩溃；取消后无遗留子进程。

---

## Week 7：CLI 与 MCP Server

**任务**

- `stepwork` CLI；
- JSON 输出和退出码；
- CLI 连接 Application Gateway；
- MCP Server stdio；
- Tools：项目、导入、分析、脚本、渲染、状态、Artifact；
- MCP Scope；
- Tool input/output Schema；
- 审计记录。

**交付**

外部 Agent 可通过 MCP 发起分析并获取 Job/Artifact。

**Gate**

CLI、UI、MCP 对同一 Command 的结果一致；MCP 无法调用未授权项目。

---

## Week 8：插件、BrandProfile 与 Provenance

**任务**

- Plugin Manifest；
- 插件加载、启停和权限展示；
- 一个示例 Provider 插件；
- BrandProfile 基础字段；
- Provenance 页面；
- AgentTask/AgentArtifact UI 占位和数据模型；
- 诊断包导出。

**交付**

用户能查看项目完整来源、模型和插件记录。

**Gate**

禁用插件后系统稳定；项目仍可打开和导出。

---

## Week 9：集成、数据迁移与种子测试

**任务**

- 全链路 E2E；
- 数据备份恢复；
- 项目导出/导入；
- 安装、升级、卸载；
- 日志脱敏；
- 5 名内部/种子用户测试；
- 修复 P0/P1 问题；
- 性能测量。

**交付**

Release Candidate 1。

**Gate**

无数据丢失、无高危安全问题、P0 流程完成率达到 80%。

---

## Week 10：公开 Alpha 准备

**任务**

- 10 名种子用户；
- Onboarding；
- 示例项目；
- 错误恢复文案；
- 隐私和许可证；
- 安全披露流程；
- Roadmap；
- 发布包签名；
- Alpha Release Notes。

**交付**

V0.1 Windows Alpha。

**Gate**

满足 PRD MVP 验收指标或有明确偏差说明和后续处理计划。

---

## 5. 工作流分解

### 5.1 Desktop/Core

- Tauri Host；
- Project UI；
- Command Bus；
- Settings；
- Credential Store；
- Update Skeleton。

### 5.2 AI/Media

- ASR；
- AI Provider；
- Analysis Schema；
- Script Generation；
- TTS；
- FFmpeg Renderer。

### 5.3 Agent Interop

- CLI；
- MCP Server；
- AgentTask；
- AgentArtifact；
- Approval Policy Skeleton。

### 5.4 Plugin

- Manifest；
- Health Check；
- Scope；
- 示例 Provider。

### 5.5 Publisher Migration

MVP 只完成：

- 审计；
- 新接口设计；
- Profile Manager PoC；
- 填写与预览技术实验。

不进入发布验收主路径。

---

## 6. MVP 核心测试矩阵

| 场景 | 数量 | 通过标准 |
|---|---:|---|
| 本地视频导入 | 30 | 100% 不崩溃，失败有错误 |
| ASR | 20 | ≥90% 完成 |
| 分析 Schema | 30 | ≥90% 合法输出 |
| 脚本版本 | 20 次编辑 | 0 丢失 |
| 渲染 | 10 连续任务 | ≥90% 成功，0 僵尸进程 |
| 应用异常退出 | 10 次 | 数据和 Job 可恢复 |
| CLI | 全 P0 命令 | 输出稳定 JSON |
| MCP | 8 个核心 Tool | Scope 和 Schema 全通过 |
| 项目导出/导入 | 5 个项目 | 数据一致 |
| 插件禁用/升级 | 10 次 | Core 不崩溃 |

---

## 7. 种子用户计划

### 7.1 用户构成

- 4 名个人创作者；
- 3 名电商/品牌运营；
- 2 名开发者或 Agent 重度用户；
- 1 名小团队负责人。

### 7.2 测试任务

每位用户完成：

1. 创建项目；
2. 导入一条真实素材；
3. 生成分析；
4. 选择原创角度；
5. 修改脚本；
6. 输出视频草稿；
7. 尝试 CLI 或 MCP（开发者用户）；
8. 提交问题和价值评分。

### 7.3 访谈问题

- 最有价值的环节；
- 最不可信的环节；
- 哪些结果必须人工修改；
- 是否愿意长期管理 BrandProfile；
- 是否愿意安装本地桌面工具；
- 是否愿意让已有 Agent 调用 STEPWORK；
- 下一次使用的真实触发条件。

---

## 8. MVP 风险与应对

### 风险：范围仍然过大

应对：视频模板只保留一个；Publisher 不进主路径；A2A/ACP 只做数据模型。

### 风险：Tauri + Python 打包不稳定

应对：Windows x64 单平台；固定 Python 和 FFmpeg；Week 1 即构建安装包。

### 风险：模型输出不稳定

应对：Schema Validation、Repair、Provider 切换和失败可编辑。

### 风险：MCP 增加复杂度

应对：CLI 与 MCP 共用 Gateway；只暴露 P0 Tools；不做远程 HTTP。

### 风险：视频渲染耗时

应对：固定模板、限制输入时长、优先 FFmpeg、明确进度。

### 风险：种子用户只尝鲜不复用

应对：7 日内安排第二次真实任务，不以首次满意度替代留存。

---

## 9. MVP 发布决策

### GO

- P0 功能可用；
- 数据和任务恢复通过；
- 10 名用户中 ≥7 完成流程；
- 无高危安全问题；
- CLI/MCP 可稳定调用；
- 核心流程不依赖官方云服务。

### CONDITIONAL GO

- 视频草稿质量一般，但脚本和 Artifact 价值明确；
- 可将 Renderer 标记为实验，继续发布 Alpha。

### NO-GO

- 项目数据丢失；
- Worker 无法恢复；
- MCP 可越权；
- 安装包在目标 Windows 环境普遍失败；
- 分析结果不能稳定落入 Schema；
- 用户无法理解项目与 Agent/Artifact 模型。

