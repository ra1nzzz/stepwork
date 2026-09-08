# STEPWORK 开源 Agent-Native AI 内容运营桌面工作台产品总纲

**文档版本：V2.0**  
**文档定位：项目最高层产品、架构与治理基线**  
**产品形态：本地优先的跨平台桌面应用**  
**开发模式：开源核心 + 开放插件生态**  
**核心特征：Agent-Native、模型中立、人工可控、全过程可追溯**  
**可持续模式：赞助、官方服务、定制开发、私有部署与长期支持**

---

# 一、执行摘要

## 1.1 产品定义

STEPWORK 是一款面向个人创作者、小型内容团队和企业内容部门的：

> **本地优先、Agent-Native、模型可替换、能力可插拔、全过程可追溯的开源 AI 内容运营桌面工作台。**

STEPWORK 帮助用户完成：

```text
参考内容与素材导入
→ 转写与结构分析
→ 选题与原创角度生成
→ 脚本创作与人工编辑
→ 配音、字幕和视频草稿生成
→ 多平台内容适配
→ 人工确认下的发布辅助
→ 发布结果与内容资产沉淀
```

与此同时，STEPWORK 不是一个只能由人点击使用的封闭桌面应用。

它同时能够：

- 被 Codex、Hermes、OpenClaw 或用户自定义 Agent 调用；

- 向研究、事实核查、翻译、设计、开发等外部 Agent 委派任务；

- 作为 MCP Server 向其他 Agent 提供工具和内容资源；

- 作为 MCP Client 使用外部工具和知识源；

- 作为 A2A Agent 与其他独立 Agent 协作；

- 通过 ACP 连接用户常用的交互式本地或远程 Agent；

- 通过 CLI 接入脚本、自动化工作流和不支持标准协议的 Agent。

具体 Agent 是否原生支持某种协议，由相应 Agent 和适配器决定，STEPWORK 不预设所有外部 Agent 都具备相同协议能力。

---

## 1.2 一句话定位

> **STEPWORK 是一个本地优先、Agent-Native、完全可扩展的开源 AI 内容运营桌面工作台，帮助创作者从参考内容和已有素材中形成原创表达，并安全地生成、管理、协作和分发内容。**

---

## 1.3 STEPWORK 不是什么

STEPWORK 不定位为：

- 爆款洗稿器；

- 去水印和素材搬运工具；

- 无人值守批量养号系统；

- 专业非线性视频剪辑软件；

- 封闭的云端自媒体 SaaS；

- 单一模型客户端；

- 单一平台自动发布脚本；

- 试图替代用户已有 Agent 的超级入口。

STEPWORK 的价值不是占有用户全部工作流，而是成为：

> **用户内容资产、专业创作能力和外部 Agent 生态之间的开放协作中枢。**

---

## 1.4 产品形态

STEPWORK 首先是一款桌面应用，而不是云端 SaaS。

平台优先级：

1. Windows 10/11；

2. macOS；

3. Linux 由官方或社区逐步支持。

桌面优先的原因：

- 素材默认保存在本地；

- API Key 和平台会话无需托管到 STEPWORK 云端；

- 转写、分析和视频渲染可使用用户本地资源；

- 平台操作从用户自己的设备和网络环境发起；

- 企业可以离线运行或私有部署；

- 用户不购买官方服务也能使用主要功能；

- 更适合承载用户已有的本地 Agent、CLI、MCP 和 ACP 进程。

---

## 1.5 开源定位

STEPWORK 不依靠永久封闭核心功能盈利。

建议采用：

> **核心代码开放，官方服务收费。**

可持续收入来自：

- 个人与企业赞助；

- 官方签名安装包和自动更新服务；

- 云端同步、模型网关或渲染服务；

- 企业私有部署；

- 行业与平台插件定制；

- 企业系统连接；

- Agent 与工作流定制；

- 培训、支持和长期维护。

---

# 二、产品愿景

## 2.1 长期愿景

STEPWORK 希望成为 AI 内容运营领域的开放基础设施：

- 像 Obsidian 一样，本地掌握数据；

- 像 Home Assistant 一样，通过插件扩展能力；

- 像专业创作工具一样，强调可编辑和可追溯；

- 像开发者工具一样，允许通过 CLI 和协议被自动化；

- 像 Agent 工作台一样，与用户已有的智能体协同，而不是重新制造一个孤岛。

长期形态不是固定功能集合，而是：

```text
内容资产核心
+ 创作工作流引擎
+ Agent 互操作层
+ 插件运行时
+ 安全执行层
+ 社区生态
```

---

## 2.2 核心战略判断

STEPWORK 的长期壁垒不应是：

- 某个模型 API；

- 某个 Prompt；

- 某个实验性 GUI Agent；

- 某个视频下载库；

- 某个平台选择器；

- 某个单独 Agent 品牌。

真正可以积累的价值包括：

1. 统一的内容资产和版本标准；

2. 创作者账号画像和品牌声音；

3. 从分析、生成、修改到采用的反馈数据；

4. Agent、模型和工具可替换的工作流体系；

5. 跨 Agent 的任务、Artifact 和权限标准；

6. 平台适配、页面状态和失败恢复经验；

7. 安全透明的人工审批机制；

8. 完整的来源、生成、协作和发布记录；

9. 活跃可信的插件与开源社区。

---

# 三、核心产品原则

## 3.1 本地优先

默认数据保存在用户本机。

云模型、云同步、云渲染和官方托管均为可选能力，不能成为核心功能的强制依赖。

---

## 3.2 Agent-Native

STEPWORK 从架构第一天开始同时面向：

- 人类用户；

- 本地 Agent；

- 远程 Agent；

- 自动化脚本；

- 企业工作流；

- 第三方工具。

UI 只是入口之一，不能成为唯一入口。

所有核心业务能力必须可以通过统一 Application Services 被 UI、CLI、MCP、A2A 和 ACP 适配层调用。

---

## 3.3 模型中立与 BYOK

用户可以使用：

- STEPFUN；

- OpenAI Compatible API；

- OpenAI；

- Gemini；

- Ollama；

- 本地模型；

- 企业内部模型；

- 社区 AI Provider。

STEPWORK 不将项目数据与任何单一模型厂商绑定。

---

## 3.4 人工最终控制

以下操作必须可预览、可暂停、可取消和可人工接管：

- 发布；

- 删除；

- 覆盖文件；

- 向外部服务上传素材；

- 调用高成本模型；

- 安装高权限插件；

- 修改平台账号；

- 执行外部 Agent 请求；

- 向其他 Agent 暴露项目数据。

外部 Agent 不得通过 CLI、MCP、A2A 或 ACP 绕过这些规则。

---

## 3.5 插件化优先

核心程序只维护稳定能力：

- 项目和内容模型；

- Agent Task 与 Artifact；

- 工作流和任务状态；

- 插件运行环境；

- 凭据和权限；

- 内容版本；

- 来源与审计；

- 导入导出标准。

变化频繁的能力放入插件：

- 视频链接解析；

- 热榜数据；

- AI 模型；

- ASR 和 TTS；

- 内容分析；

- 视频模板；

- 平台发布；

- 数据回流；

- 外部 Agent Adapter。

---

## 3.6 原创辅助

系统可以提取参考内容的可观察结构：

- 开场；

- 叙事；

- 节奏；

- 镜头；

- 情绪；

- 互动设计。

但不把这些分析包装成确定的“爆款公式”，也不鼓励直接复制原作。

生成结果优先结合：

- 用户自己的观点；

- 品牌声音；

- 历史内容；

- 目标受众；

- 自有素材；

- 多个来源；

- 外部 Agent 的结构化研究结果。

---

## 3.7 Artifact 优先

Agent、模型和工作流产生的重要结果，不能只存在于聊天记录中。

所有可复用结果应转化为结构化 Artifact：

- 分析报告；

- 选题；

- 脚本；

- 研究结果；

- 翻译稿；

- 图片；

- 音频；

- 视频；

- 代码补丁；

- 发布记录。

---

## 3.8 透明执行

用户应随时知道：

- 哪个 Agent 正在工作；

- 使用了哪个模型；

- 调用了哪个插件；

- 上传了哪些数据；

- 预计产生多少费用；

- 任务执行到哪一步；

- 哪些内容来自外部 Agent；

- 哪些结论尚未验证；

- 最终发布到了哪个账号。

---

# 四、目标用户

## 4.1 个人创作者

典型特征：

- 已经开始稳定更新；

- 每周生产 2—10 条内容；

- 从选题到脚本耗时较长；

- 经常需要参考同行内容；

- 不希望全部素材和账号数据进入云端；

- 已经在使用一个或多个 AI Agent。

主要价值：

- 快速分析参考内容；

- 减少从零写稿；

- 维持个人表达；

- 调用自己熟悉的 Agent；

- 快速形成可编辑视频草稿；

- 保持内容资产长期沉淀。

---

## 4.2 1—5 人内容团队

主要需求：

- 项目与素材管理；

- 脚本版本；

- 品牌声音；

- 内容审核；

- 多平台版本；

- Agent 分工；

- 发布记录；

- 权限和账号隔离。

---

## 4.3 企业内容和电商团队

主要需求：

- 企业知识库；

- 商品与 SKU 资料；

- 品牌规范；

- 敏感词检查；

- 审批；

- 企业内部模型和 Agent；

- 私有部署；

- CMS、PIM、飞书、企业微信等系统连接；

- 多品牌和多项目隔离。

该类用户是定制开发和长期服务的主要付费来源。

---

## 4.4 暂不优先覆盖

早期不重点服务：

- 上百账号的无人值守矩阵；

- 纯搬运和批量洗稿；

- 专业影视制作；

- 复杂非线性剪辑；

- 高风险平台自动化；

- 依赖设备指纹伪装和风控规避的业务。

---

# 五、核心使用场景

## 5.1 参考内容转原创脚本

```text
导入视频或链接
→ 转写
→ 结构分析
→ 生成多个原创角度
→ 结合账号画像
→ 生成脚本
→ 用户编辑
→ 保存版本
```

---

## 5.2 用户已有素材整理

```text
导入拍摄素材、语音或逐字稿
→ 摘要和观点提取
→ Agent 补充研究
→ 组织脚本
→ 生成字幕
→ 输出视频草稿
```

---

## 5.3 多 Agent 内容生产

```text
用户提出内容目标
→ STEPWORK 创建主任务
→ Research Agent 收集资料
→ Fact-check Agent 核查事实
→ STEPWORK 生成脚本
→ Brand Agent 检查品牌声音
→ Renderer 输出视频草稿
→ 用户最终审核
```

---

## 5.4 外部 Agent 调用 STEPWORK

```text
用户在已有 Agent 中发起任务
→ Agent 通过 MCP/CLI/A2A 调用 STEPWORK
→ STEPWORK 分析素材或生成草稿
→ 产出结构化 Artifact
→ Agent 获取结果继续处理
```

---

## 5.5 STEPWORK 连接用户常用 Agent

```text
用户在 STEPWORK 中打开 Agent 面板
→ 通过 ACP 启动或连接 Agent
→ Agent 获得当前项目的受控上下文
→ Agent 请求工具或文件权限
→ 用户审批
→ Agent 产生补丁、报告或其他 Artifact
```

---

## 5.6 发布辅助

```text
选择平台和账号
→ Publisher 插件打开创作者后台
→ 上传素材并填写内容
→ 生成发布前预览
→ 用户确认
→ 执行发布
→ 保存结果和证据
```

---

# 六、核心功能架构

## 6.1 项目与内容中心

主要功能：

- 创建 Workspace；

- 创建内容项目；

- 导入视频、音频、图片、文本和链接；

- 素材来源记录；

- 项目标签和文件夹；

- 内容版本；

- 自动保存；

- 版本比较；

- 项目搜索；

- 项目打包导出；

- 本地备份和恢复；

- 缓存和彻底删除。

核心要求：

> 每次分析、生成、Agent 协作、编辑、渲染和发布都必须关联明确项目和版本。

---

## 6.2 参考内容分析

输入方式：

- 本地视频；

- 本地音频；

- 字幕；

- 逐字稿；

- 视频链接；

- 网页；

- 少量批量参考内容。

输出：

- 摘要；

- 核心观点；

- 开场；

- 段落或镜头结构；

- 叙事方式；

- 信息密度；

- 情绪变化；

- 互动点；

- 时间戳；

- 可借鉴策略；

- 风险提示；

- 原始来源。

分析模式：

### 快速模式

直接由多模态模型进行整体理解。

### 精确模式

```text
ASR 精确转写
+ 场景切分和关键帧
+ 多模态模型综合分析
```

---

## 6.3 选题与原创角度

输入来源：

- 参考内容；

- 用户观点；

- 热点；

- 商品资料；

- 评论；

- 品牌知识库；

- 历史内容；

- 外部 Agent Artifact。

输出：

- 三至五个原创角度；

- 目标受众；

- 核心观点；

- 推荐内容形式；

- 开场建议；

- 与来源差异；

- 风险；

- 制作难度；

- 推荐原因。

---

## 6.4 AI 创作工作台

主要功能：

- 结构化脚本；

- 标题和开场；

- 正文和 CTA；

- 段落级重写；

- 长度调整；

- 语气调整；

- 观点强化；

- 事实核查入口；

- 来源引用；

- 相似度提醒；

- 脚本版本；

- Agent 修改记录；

- Markdown 和纯文本导出。

首阶段模板：

1. 口播；

2. 知识讲解；

3. 产品介绍；

4. 观点评论；

5. 盘点；

6. 问答；

7. 简单图文视频。

---

## 6.5 品牌声音与账号画像

每个账号可以配置：

- 定位；

- 受众；

- 内容支柱；

- 常用语气；

- 常用句式；

- 观点边界；

- 禁用表达；

- 品牌词典；

- 产品资料；

- 历史脚本；

- 已发布选题；

- 用户最终采用、修改和删除记录。

---

## 6.6 配音、字幕与视频草稿

通过 Speech Provider 和 Renderer 插件提供：

- 云端或本地 TTS；

- 用户录音；

- ASR；

- 字幕时间对齐；

- 专有名词；

- 字幕样式；

- BGM；

- 固定视频模板；

- MP4、音频、字幕和封面导出。

首阶段模板：

- 字幕口播；

- 背景素材加旁白；

- 图文快闪；

- 产品图片轮播；

- 简单片头片尾。

产品统一称为：

> **可编辑视频草稿。**

---

## 6.7 平台内容适配

针对不同平台生成独立版本：

- 标题；

- 正文；

- 标签；

- 话题；

- 简介；

- 封面文案；

- 评论引导；

- 格式和字数检查。

平台版本独立保存，不覆盖主稿。

---

## 6.8 发布助手

### Level 1：导出

输出完整发布素材，由用户手动发布。

### Level 2：填写与预览

插件自动上传和填写，但不点击最终发布。

这是默认模式。

### Level 3：确认后发布

用户确认最终预览后，系统获得一次性发布授权。

### Level 4：受控自动发布

只作为实验性能力，在明确策略、账号、时间和内容范围内运行。

---

# 七、Agent-Native 互操作架构

## 7.1 协议职责分工

| 接口  | 主要用途                        | STEPWORK 角色     |
| --- | --------------------------- | --------------- |
| CLI | 脚本、自动化、确定性调用                | 提供方             |
| MCP | Agent 调用工具、数据和资源            | Server + Client |
| A2A | 独立 Agent 之间委派任务和交换 Artifact | Client + Server |
| ACP | 桌面客户端连接交互式 Agent            | Client/Host     |

MCP 的核心 Server 原语包括 Tools、Resources 和 Prompts；A2A 主要面向独立 Agent 的能力发现、任务管理和 Artifact 交换，两者是互补关系。

ACP 在本文中专指 **Agent Client Protocol**。其本地 Agent 通常作为客户端子进程运行，通过 JSON-RPC over stdio 连接；远程 Agent 可通过 HTTP 或 WebSocket，相关远程能力仍在演进。

---

## 7.2 Agent Interoperability Layer

```text
┌─────────────────────────────────────────────────────┐
│ Desktop UI │ CLI │ MCP │ A2A │ ACP │ Plugin API    │
└───────────────┬─────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────┐
│ Agent Interoperability Layer                        │
│                                                     │
│ 身份 / 能力发现 / Schema / 权限 / Artifact          │
│ 任务追踪 / 审批 / 审计 / 协议适配 / 错误映射        │
└───────────────┬─────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────┐
│ STEPWORK Application Services                       │
│                                                     │
│ Project / Analyze / Generate / Render / Publish     │
└───────────────┬─────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────┐
│ Core / Worker / Plugin Runtime / Publisher Engine   │
└─────────────────────────────────────────────────────┘
```

所有入口必须复用同一套 Application Services。

不得为 UI、CLI、MCP、A2A 和 ACP 分别实现独立业务逻辑。

---

## 7.3 CLI

建议命令：

```bash
stepwork workspace list
stepwork project create
stepwork project show <id>
stepwork source import <file-or-url>
stepwork analyze create <project-id>
stepwork topic propose <project-id>
stepwork script generate <project-id>
stepwork render create <content-version-id>
stepwork job status <job-id>
stepwork artifact export <artifact-id>
stepwork publish prepare <variant-id>
```

CLI 必须支持：

- `--json`；

- 稳定退出码；

- stdin；

- stdout/stderr 分离；

- Job ID；

- 幂等键；

- 非交互模式；

- 权限错误；

- 不要求桌面窗口正在打开。

CLI 是最早实现、最稳定的机器入口。

---

## 7.4 MCP Server

建议第一批 Tools：

```text
list_projects
get_project
import_source
analyze_source
propose_topics
generate_script
create_platform_variant
render_draft
get_job_status
list_artifacts
export_artifact
prepare_publish
```

建议 Resources：

```text
stepwork://workspace/{id}
stepwork://project/{id}
stepwork://source/{id}
stepwork://analysis/{id}
stepwork://content-version/{id}
stepwork://artifact/{id}
stepwork://brand-profile/{id}
```

建议 Prompts：

```text
analyze-reference-content
generate-original-angles
rewrite-in-brand-voice
adapt-content-for-platform
review-content-risk
```

高风险操作不直接作为无审批工具暴露。

---

## 7.5 MCP Client

STEPWORK 可以连接外部 MCP Server：

- 搜索；

- 文件库；

- 企业知识库；

- 商品资料；

- GitHub；

- 数据库；

- 数据分析；

- 内部系统。

外部返回内容必须记录来源和信任级别。

---

## 7.6 A2A

STEPWORK 将自身暴露为内容专业 Agent。

A2A 当前规范围绕 Agent Card、Task、Message、Part 和 Artifact 等核心概念展开，并支持长时间任务和人工参与场景。

建议 Skills：

```text
content-reference-analysis
original-topic-proposal
script-drafting
brand-voice-rewriting
platform-content-adaptation
subtitle-generation
media-draft-rendering
publish-preparation
```

STEPWORK 同时可向外部 Agent 委派：

- 研究；

- 事实核查；

- 翻译；

- SEO；

- 品牌审阅；

- 数据分析；

- 插件开发；

- 发布适配器诊断。

---

## 7.7 ACP

ACP 用于 STEPWORK Desktop 与用户常用交互式 Agent 的协作。

STEPWORK 作为 ACP Client/Host，负责：

- 启动本地 Agent 子进程；

- 建立 Session；

- 展示流式进度；

- 接收权限请求；

- 向 Agent 提供 STEPWORK MCP Server；

- 显示文件差异和产物；

- 允许暂停、取消和切换 Agent；

- 将 Agent Session 与项目关联。

重点场景：

1. 开发和修复插件；

2. 诊断 Publisher 失效；

3. 修改模板和配置；

4. 让用户已有 Agent 在项目上下文继续工作；

5. 把 Agent 输出沉淀为 STEPWORK Artifact。

ACP 主要解决：

```text
STEPWORK Desktop ↔ Interactive Agent
```

A2A 主要解决：

```text
STEPWORK Agent ↔ Independent Agent
```

---

## 7.8 Agent 数据模型

新增实体：

### AgentConnection

- Agent ID；

- 协议；

- 本地或远程；

- 信任等级；

- 权限；

- 状态。

### AgentCapability

记录 Agent 可以提供的能力。

### AgentTask

- 发起方；

- 接收方；

- 输入；

- 状态；

- 超时；

- 成本；

- 审批；

- 重试。

### AgentSession

记录多轮 Agent 交互。

### AgentArtifact

记录 Agent 产生的文档、结构化数据、图片、音视频或代码补丁。

### InvocationContext

记录调用来源、用户、Workspace、权限和数据外传策略。

### ApprovalRequest

记录 Agent 请求执行的高风险操作。

---

## 7.9 Artifact Schema

```json
{
  "artifactId": "artifact-001",
  "type": "content-analysis",
  "schemaVersion": "1",
  "producer": {
    "agentId": "research-agent",
    "protocol": "a2a"
  },
  "projectId": "project-001",
  "content": {},
  "sources": [],
  "createdAt": "ISO-8601",
  "trustLevel": "external-unverified"
}
```

Artifact 应包含：

- 类型；

- Schema 版本；

- 来源；

- 生产者；

- 项目归属；

- 时间；

- 信任等级；

- 内容哈希；

- 后续修改记录。

大型音视频使用文件引用，不直接嵌入 Agent 消息。

---

# 八、桌面技术架构

## 8.1 推荐技术栈

### 桌面宿主

- Tauri 2；

- Rust；

- Tauri Commands 和 Events；

- Capabilities；

- 系统密钥链或安全存储；

- 自动更新；

- 签名安装包。

Tauri 支持将 Python CLI 或本地服务打包为 Sidecar，也提供基于窗口和 WebView 的细粒度 Capabilities 权限控制，适合 STEPWORK 的本地 Worker 和高权限桌面边界。

### 前端

- React；

- TypeScript；

- Vite；

- Zustand；

- TanStack Query；

- shadcn/ui；

- Tailwind CSS；

- TipTap 或 Lexical。

### 本地业务层

- Python 3.12；

- Pydantic；

- SQLAlchemy 或 SQLModel；

- SQLite；

- 独立 Worker；

- JSON Schema。

### 音视频

- FFmpeg；

- yt-dlp；

- ASR Provider；

- OpenCV，仅用于精确模式；

- SRT/ASS。

---

## 8.2 进程结构

```text
┌─────────────────────────────────────────────┐
│ STEPWORK Desktop                            │
│ React + TypeScript                          │
│                                             │
│ 项目 / Agent / 分析 / 脚本 / 渲染 / 发布   │
└─────────────────────┬───────────────────────┘
                      │
┌─────────────────────▼───────────────────────┐
│ Tauri Rust Host                             │
│                                             │
│ 权限 / 凭据 / 插件 / Agent / Sidecar / 更新│
└─────────────┬───────────────────┬───────────┘
              │                   │
┌─────────────▼───────┐  ┌────────▼───────────┐
│ STEPWORK Worker    │  │ Publisher Engine   │
│                    │  │                    │
│ AI / ASR / TTS     │  │ CDP / Playwright   │
│ Agent Adapters     │  │ DOM / 上传 / 平台  │
│ 分析 / 渲染        │  │                    │
└────────────────────┘  └────────────────────┘
```

---

## 8.3 统一 Command Bus

所有业务请求先转换为内部 Command：

```text
CreateProject
ImportSource
AnalyzeSource
GenerateTopics
GenerateScript
CreateRenderJob
PreparePublish
ApprovePublish
InvokeAgent
AcceptArtifact
```

调用来源统一记录：

```text
UI
CLI
MCP
A2A
ACP
PLUGIN
SCHEDULED
```

---

## 8.4 本地任务系统

使用：

- SQLite 任务表；

- 独立 Worker；

- 持久化状态；

- 进度事件；

- 并发限制；

- 取消和重试；

- 应用重启恢复；

- 临时文件清理。

状态：

```text
PENDING
PREPARING
DOWNLOADING
TRANSCRIBING
ANALYZING
DELEGATING
WAITING_AGENT
GENERATING
SYNTHESIZING
RENDERING
WAITING_USER
PUBLISHING
VERIFYING
COMPLETED

FAILED_RETRYABLE
FAILED_FINAL
CANCELLED
```

---

# 九、插件体系

## 9.1 插件分类

### Source Provider

本地文件、链接、网页、RSS、热点和知识库。

### AI Provider

文本、多模态、图像、Embedding 和内部模型。

### Speech Provider

ASR、TTS、时间对齐和声音克隆。

### Analyzer

结构分析、相似度、敏感词、事实检查和质量评测。

### Renderer

字幕、视频模板、封面和音频处理。

### Publisher

内容验证、填写、预览、发布和结果确认。

### Exporter

Markdown、JSON、SRT、MP4、剪辑项目和 CMS。

### Agent Adapter

连接不原生支持 CLI、MCP、A2A 或 ACP 的特定 Agent。

---

## 9.2 Manifest

```json
{
  "id": "org.stepwork.publisher.example",
  "name": "Example Publisher",
  "version": "0.1.0",
  "apiVersion": "1",
  "type": "publisher",
  "license": "Apache-2.0",
  "entry": "plugin",
  "permissions": [
    "network:creator.example.com",
    "storage:plugin-data",
    "browser:control",
    "files:read-selected"
  ],
  "capabilities": [
    "content-validation",
    "fill-and-preview"
  ],
  "platforms": [
    "windows",
    "macos"
  ]
}
```

---

## 9.3 权限模型

插件必须声明：

- 网络域名；

- 文件访问；

- 浏览器控制；

- Cookie 和会话；

- 密钥；

- 外部程序；

- 数据上传；

- Agent 调用；

- 发布权限。

升级增加权限时必须重新授权。

---

## 9.4 信任等级

- Official；

- Verified；

- Community；

- Experimental；

- Local。

---

## 9.5 插件隔离

优先顺序：

1. 声明式插件；

2. 受限 Worker；

3. 独立子进程；

4. 严格审查的原生插件。

Publisher 和 Agent Adapter 等高权限插件不得默认运行在桌面主进程中。

---

# 十、Media Auto Pilot 重构方案

## 10.1 定位

现有 `media-auto-pilot` 已实现 CDP 浏览器持久化、多策略上传、DOM 定位、平台适配器和抖音发布原型，可作为 STEPWORK 发布执行层的代码基础。

它将被重构为：

> **STEPWORK Browser Publisher Engine**

不直接作为 STEPWORK Core。

---

## 10.2 保留能力

- Chrome/CDP Session；

- 浏览器登录状态复用；

- DOM Snapshot；

- Smart Locator；

- 多策略文件上传；

- 平台适配器思想；

- YAML 选择器配置；

- 发布截图；

- 发布结果验证；

- CLI 诊断入口。

现有平台基类已经定义登录、发布和工作流等接口，可作为 Publisher SDK 的初始参考。

---

## 10.3 必须重写

- 固定 CDP 端口；

- 全局共享浏览器目录；

- Linux 云服务器专用实现；

- 只返回布尔值的接口；

- 硬编码平台表；

- CLI 与核心逻辑耦合；

- 无一次性发布授权；

- 上传验证失败时默认成功；

- 自动点击最终发布。

---

## 10.4 必须删除

现有实现包含隐藏 WebDriver、Canvas 和 WebGL 指纹修改等反检测代码。

这些能力不进入 STEPWORK 正式架构。

正式原则：

> 使用用户真实设备、真实浏览器 Profile、真实登录状态和明确授权进行透明辅助操作。

---

## 10.5 新 Publisher 接口

```text
health_check
login
validate_content
prepare
fill
preview
publish
verify
```

结果不得只使用 `True/False`，必须返回：

- 状态；

- 错误码；

- 人工输入需求；

- 进度；

- 证据；

- 可否重试。

---

# 十一、核心数据模型

## 内容实体

- Workspace；

- UserProfile；

- BrandProfile；

- SocialAccount；

- ContentProject；

- SourceAsset；

- Transcript；

- AnalysisReport；

- TopicProposal；

- ContentVersion；

- PlatformVariant；

- RenderJob；

- PublishJob；

- ExecutionAttempt；

- ProvenanceRecord。

## Agent 实体

- AgentConnection；

- AgentCapability；

- AgentSession；

- AgentTask；

- AgentArtifact；

- InvocationContext；

- ApprovalRequest。

## 插件实体

- PluginInstallation；

- PluginPermissionGrant；

- PluginExecution；

- PluginHealthStatus。

---

# 十二、本地数据结构

```text
STEPWORK_HOME/
├── stepwork.db
├── projects/
│   └── <project-id>/
│       ├── project.json
│       ├── sources/
│       ├── transcripts/
│       ├── analysis/
│       ├── scripts/
│       ├── artifacts/
│       ├── renders/
│       └── exports/
├── agents/
├── browser-profiles/
├── plugins/
├── plugin-data/
├── models/
├── cache/
├── logs/
├── backups/
└── config/
```

原则：

- 项目可独立导出；

- 原始数据和缓存分离；

- 浏览器 Profile 按账号隔离；

- 日志不记录 Key、Cookie、验证码和私信；

- 用户可以彻底删除项目；

- 外部 Agent Artifact 保留来源和信任级别。

---

# 十三、安全与隐私

## 13.1 凭据安全

API Key、Token 和平台凭据不得明文写入普通文件。

使用：

- Windows Credential Manager；

- macOS Keychain；

- Linux Secret Service；

- Tauri Stronghold 或同等安全存储。

---

## 13.2 Agent 权限

权限示例：

```text
project:read
project:write
source:read
source:export
analysis:create
script:write
render:create
network:research
publisher:prepare
publisher:execute
plugin:install
```

`publisher:execute` 默认不授予外部 Agent。

---

## 13.3 高风险操作

以下操作仅允许 Agent 发起准备请求：

```text
publish.prepare
delete.prepare
export.prepare
upload.prepare
plugin.install.prepare
credential.use.prepare
```

真正执行需要：

- 用户即时确认；

- 或预先定义的、可审计的受控策略。

---

## 13.4 本地服务安全

CLI、MCP、A2A 和 ACP 本地端点默认：

- 只监听 Loopback；

- 不自动暴露公网；

- 不返回 API Key 或 Cookie；

- 远程访问必须显式开启；

- 使用短期 Token；

- 限制 Scope；

- 记录调用审计。

---

## 13.5 Prompt Injection 防护

外部 Agent、网页和参考内容全部视为不可信输入。

系统必须区分：

- 用户指令；

- 项目资料；

- 网页内容；

- Agent 输出；

- 工具输出；

- 系统策略。

外部内容不得自行提升权限或触发发布。

---

# 十四、来源、原创与合规

## 14.1 权利记录

导入内容时记录：

- 原始链接；

- 作者或来源；

- 导入时间；

- 权利声明；

- 临时文件策略。

不把去水印、搬运和规避版权检测作为能力。

---

## 14.2 Provenance

记录：

- 使用了哪些模型；

- 使用了哪些 Agent；

- 使用了哪些来源；

- 哪些段落由用户修改；

- 哪个插件完成渲染；

- 哪个版本被发布；

- AI 标识状态。

---

## 14.3 外部 Agent 信任等级

建议等级：

```text
trusted-local
trusted-remote
verified-external
external-unverified
generated-unverified
human-reviewed
```

未经核查的外部 Agent 结果不得自动升级为事实。

---

# 十五、开源许可证与治理

## 15.1 建议许可证

| 部分                           | 建议许可证             |
| ---------------------------- | ----------------- |
| STEPWORK Core                | AGPL-3.0-or-later |
| Desktop UI                   | AGPL-3.0-or-later |
| Python Worker                | AGPL-3.0-or-later |
| Agent Interoperability Layer | AGPL-3.0-or-later |
| Browser Publisher Engine     | AGPL 或独立 MIT      |
| Plugin SDK                   | Apache-2.0        |
| Agent Adapter SDK            | Apache-2.0        |
| 示例插件                         | Apache-2.0        |
| 文档与模板                        | CC BY-SA 4.0      |
| 名称与 Logo                     | 保留商标权             |

---

## 15.2 Media Auto Pilot 许可证

当前 README 标注 MIT。

正式重构前必须：

1. 补充正式 LICENSE；

2. 核验著作权归属；

3. 检查复制或派生代码；

4. 建立依赖清单；

5. 决定保留 MIT 独立组件，还是依法迁移到 AGPL。

稳妥方案是：

```text
Media Auto Pilot / Publisher Engine：独立 MIT
STEPWORK Core：AGPL
双方通过 RPC 和插件接口连接
```

---

## 15.3 仓库治理文件

必须包含：

```text
LICENSE
THIRD_PARTY_NOTICES
SBOM
SECURITY.md
CONTRIBUTING.md
CODE_OF_CONDUCT.md
GOVERNANCE.md
TRADEMARK.md
ROADMAP.md
```

---

# 十六、项目可持续模式

## 16.1 赞助

权益可以包括：

- Preview/Nightly；

- 实验功能提前体验；

- Roadmap 提案；

- Issue 优先分诊；

- 线上交流；

- 官方模型或渲染额度；

- Sponsor 标识；

- 新 Agent Adapter 提前体验。

不得锁定：

- 安全修复；

- 严重 Bug 修复；

- 数据恢复；

- 合规能力；

- 必要迁移工具。

---

## 16.2 定制开发

主要方向：

- 企业私有部署；

- 企业内部 Agent 接入；

- MCP/A2A Gateway；

- CMS/PIM/知识库连接器；

- 行业工作流；

- Publisher 插件；

- 企业模型接入；

- 审批和审计；

- 培训和长期维护。

---

## 16.3 官方服务

未来可提供：

- 签名安装包；

- 自动更新；

- 云同步；

- 团队协作；

- 官方模型网关；

- 云端渲染；

- Agent Gateway；

- 企业插件仓库；

- 托管实例。

不购买服务的用户仍可使用完整本地核心。

---

# 十七、版本路线图

## Phase 0：架构与代码 Spike

**周期：1—2 周**

完成：

- Monorepo；

- Tauri + React；

- Python Sidecar；

- SQLite 任务系统；

- Command Bus；

- AgentTask 和 AgentArtifact；

- InvocationContext；

- CLI 骨架；

- Media Auto Pilot 审计；

- Publisher Engine 抽取设计；

- 许可证审计；

- 本地视频分析 Demo。

---

## V0.1：核心创作闭环与机器接口

**周期：4—6 周**

范围：

- Windows Alpha；

- 项目中心；

- 本地素材导入；

- 一个云 AI Provider；

- 一个本地 Provider；

- ASR；

- 内容分析；

- 原创角度；

- 脚本编辑器；

- 一个视频模板；

- Artifact；

- Provenance；

- 稳定 CLI；

- 基础 MCP Server；

- 任务状态查询。

不包含：

- 热榜；

- 自动发布；

- 团队协作；

- 完整 A2A；

- ACP；

- 插件市场。

---

## V0.2：插件 SDK 与 Publisher Engine

**周期：4—6 周**

范围：

- Provider SDK；

- Renderer SDK；

- Publisher SDK；

- Agent Adapter SDK；

- 插件权限；

- Browser Publisher Engine；

- 抖音填写与预览；

- MCP Client；

- 品牌声音；

- macOS 测试版。

---

## V0.3：Agent 协作与发布确认

**周期：4—6 周**

范围：

- A2A Agent Card；

- A2A Server；

- A2A Client；

- Agent Task Inbox；

- Artifact Inbox；

- Agent Connections；

- 基础 ACP Client；

- 本地 Agent 子进程；

- 一次性发布授权；

- 人工确认发布；

- 发布证据。

A2A 的 Agent Card、Task 与 Artifact 模型可直接映射 STEPWORK 的 AgentConnection、AgentTask 和 AgentArtifact。

---

## V0.4：团队与企业

**周期：4—8 周**

范围：

- 多 Workspace；

- 权限；

- 审批；

- 企业知识库；

- 企业 Agent Gateway；

- 私有插件仓库；

- 审计；

- 配置分发；

- 云同步可选服务。

---

## V1.0：稳定开放平台

**预计累计周期：5—8 个月**

目标：

- Windows 和 macOS 正式发行；

- Linux 社区版；

- 完整插件 SDK；

- 稳定 CLI/MCP；

- 基础 A2A/ACP；

- 5—10 个官方插件；

- 2—5 个 Agent Adapter；

- 插件签名；

- 自动更新；

- 完整开发文档；

- 20—50 名稳定种子用户；

- 至少 3 个企业或行业定制案例。

---

# 十八、成功指标

## 产品指标

- 首次项目完成率；

- 首次价值时间；

- 分析转脚本率；

- 脚本保存率；

- 视频草稿导出率；

- 7 日和 30 日留存；

- 每周完成项目数。

## Agent 指标

- 外部 Agent 调用成功率；

- Agent Artifact 采用率；

- 委派任务完成率；

- 人工审批通过率；

- Agent 结果返工率；

- 协议兼容失败率；

- Agent 调用产生的数据泄漏事故数。

## 工程指标

- Worker 恢复率；

- 应用崩溃率；

- 数据迁移成功率；

- 插件安装失败率；

- 发布重复事故数；

- MCP/A2A/ACP 会话成功率；

- 任务 P50/P95 延迟。

## 社区指标

- 有效 Issue；

- 外部 PR；

- 活跃贡献者；

- 独立插件；

- Agent Adapter 数量；

- 文档贡献；

- 插件维护频率。

---

# 十九、主要风险

## 产品被理解为洗稿工具

通过原创角度、来源记录、品牌声音和相似度提示控制。

## Agent 协议变化

内部 Command、Task 和 Artifact 模型保持协议中立，外部协议通过 Adapter 接入。

## 多种接口导致业务重复

所有入口统一进入 Application Services，禁止协议层直接操作数据库和 Worker。

## 外部 Agent 越权

采用独立 Scope、Approval Center、短期 Token 和完整审计。

## Publisher 失效

插件独立升级、选择器外置、失败快照和人工操作兜底。

## 插件安全

最小权限、隔离进程、签名和信任等级。

## 桌面打包复杂

Windows 优先、固定 Python 版本、Sidecar 自检和分阶段跨平台。

## 开源但无人贡献

官方承担核心开发，提供清晰 SDK、示例和 Good First Issue。

## 赞助不足

赞助不作为唯一收入，优先获得定制、部署与支持项目。

---

# 二十、项目文档体系

## 产品与架构

```text
PRODUCT_CHARTER.md
PRD.md
ARCHITECTURE.md
DATA_MODEL.md
SECURITY.md
```

## 插件

```text
PLUGIN_SPEC.md
PUBLISHER_SPEC.md
PLUGIN_SECURITY_SPEC.md
```

## Agent 互操作

```text
AGENT_INTEROP_SPEC.md
CLI_SPEC.md
MCP_SPEC.md
A2A_SPEC.md
ACP_CLIENT_SPEC.md
AGENT_SECURITY_SPEC.md
AGENT_INTEROP_PLAN.md
```

## Media Auto Pilot 迁移

```text
MIGRATION_ASSESSMENT.md
PUBLISHER_ENGINE_ARCHITECTURE.md
LICENSE_AUDIT.md
MIGRATION_PLAN.md
```

---

# 二十一、正式决策清单

1. STEPWORK 采用桌面优先路线；

2. Windows 首发，macOS 第二；

3. 使用 Tauri 2 + React + Rust Host + Python Sidecar；

4. STEPWORK 是 Agent-Native 产品；

5. UI、CLI、MCP、A2A 和 ACP 共用 Application Services；

6. CLI 是最早和最稳定的机器入口；

7. MCP 用于 Agent 与工具、资源连接；

8. A2A 用于独立 Agent 的任务协作；

9. ACP 用于桌面客户端与交互式 Agent 协作；

10. 所有重要 Agent 结果落为 Artifact；

11. Agent 不能绕过人工审批；

12. Core 不绑定特定模型或 Agent；

13. Agent Adapter 进入插件体系；

14. 本地优先并支持 BYOK；

15. 核心使用 AGPL，插件和 Agent SDK 使用 Apache-2.0；

16. `media-auto-pilot` 重构为 Browser Publisher Engine；

17. 删除指纹伪装和风控规避能力；

18. 发布默认采用填写与预览；

19. V0.1 优先完成分析、原创脚本、视频草稿、CLI 和 MCP；

20. A2A 和 ACP 在内部数据模型中前置，在后续版本逐步开放；

21. 赞助者可以提前体验实验能力，但成熟能力最终开源；

22. 主要可持续收入来自定制、部署、Agent 集成和长期支持。

---

# 二十二、最终定义

STEPWORK 最终不是一个“带 AI 的自媒体工具”，也不是一个试图替代所有 Agent 的超级应用。

它是：

> **一个由创作者掌握数据、由 Agent 共同参与、由插件持续扩展、由人类保留最终控制权的开放内容运营工作台。**

它既可以独立完成内容分析、创作和发布辅助，也能成为 Codex、Hermes、OpenClaw 或其他 Agent 工作流中的内容专业节点。

STEPWORK 的长期价值，不是它内置多少模型和平台，而是：

- 能否管理用户长期内容资产；

- 能否理解用户的表达与选择；

- 能否让不同 Agent 安全协作；

- 能否把聊天结果沉淀为可复用 Artifact；

- 能否让模型、工具和平台自由替换；

- 能否在自动化与人工控制之间保持可信边界；

- 能否形成一个开放、稳定和可持续的内容插件生态。
