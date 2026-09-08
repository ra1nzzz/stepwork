# STEPWORK 重定位：短视频创作工厂

> **Status:** Active（本文件是当前唯一的产品纲领）
> **Version:** 1.0 · **Date:** 2026-09-08
> **取代：** `docs/archive/legacy/PRODUCT_CHARTER.md`、`PRD.md`、`STRATEGY_PLAN.md` 等（见 §9 归档）

---

## 1. 一句话定位

> **STEPWORK 是一间 Agent 原生的短视频创作工厂：从选题开始，到成片结束。**

三个关键词：

| 关键词 | 含义 |
|---|---|
| **短视频创作工厂** | 不是编辑器、不是素材库。它是一条**流水线**：选题 → 文案 → 配音 → 配图 → 渲染 → 发布。产出物是成片，不是文档 |
| **Agent 原生** | 既能操作别的 Agent，也能被别的 Agent 操作。GUI 与 CLI 同为一等公民，二者都是同一套能力的外壳 |
| **选题驱动** | 入口是「做什么选题」，不是「我有一段素材」。素材是流水线的**可选输入**，不再是起点 |

### 与旧定位的区别（重要）

```text
旧（素材驱动）：导入素材 → 分析 → 脚本 → 渲染 → 导出
新（选题驱动）：发现选题 → 文案 → 配音 → 配图 → 渲染 → 发布
                   ↑ 新增            ↑ 新增   ↑ 新增
```

这不是加功能，是**换入口**。旧流程降级为「已有素材时的可选捷径」，不再是主路径。

---

## 2. 北极星目标

> **一个人，一条指令，出一條成片。**

衡量标准（按优先级）：

1. **端到端耗时**：从选题到可发布成片 ≤ 15 分钟（当前人工流水线约 40 分钟）
2. **无人值守率**：全流程中需要人工决策的次数 ≤ 2 次（选题确认、成片确认）
3. **风格可迁移**：切换创作者风格后，同一选题能产出风格可辨识的不同成片

---

## 3. 六项不可违背原则

### P1. 继续 AGPL-3.0-or-later

`worker/`、`apps/desktop/`、`core/`、`publisher-engine/` 保持 AGPL-3.0-or-later；`sdk/*` Apache-2.0；`schemas/` CC0；`docs/` CC BY-SA。

> ⚠️ **时点约束**：`git shortlog -sn --all` 显示 136 commits 全部为 ra1nzzz 一人。AGPL 目前不构成障碍（唯一著作权人可随时双许可）。
> **但在接受第一个外部 PR 之前，必须先定好 CLA 或双许可策略**，否则永久锁死 AGPL。这是有时限的事。

### P2. 热点追踪走独立 MCP Server

热点发现**不做进 STEPWORK 主仓**，做成独立 MCP Server，通过已有的 `AddMcpServer` / `CallMcpTool` 接入。

理由：① 不改核心，契合 ADR-007 cli-mcp-first；② 该 Server 可独立演进、独立发布；③ 需求真伪未验证前，独立形态试错成本最低。

> 验证通过后（确认高频使用），可考虑将稳定连接器下沉为 `worker/runtime/discovery/` 内置模块。

### P3. Agent 原生（双向）

- **出站**：能调用其它 Agent（已具备：A2A 客户端、ACP 客户端、出站 MCP 客户端）
- **入站**：能被其它 Agent 调用（已具备：MCP Server 9 只读工具、命令总线）
- **原则**：任何一个用户能在 GUI 上做的操作，Agent 必须能通过命令完成；反之亦然

### P4. GUI 与 CLI 同为一等公民

二者**不是**主次关系，是同一能力层的两个外壳。

- 新增能力必须**同时**暴露到命令总线（CLI/Agent 可达）与 GUI
- 禁止「GUI 能点但 CLI 做不到」或「CLI 有但 GUI 找不到」
- CLI 是 Agent 原生的落点：`stepwork-cli` 与 MCP Server 共享同一套命令契约

### P5. 生态互通以 YT-Agent-Ontology 为准

**不得自行重新定义** Agent / Task / Workflow / Skill / Artifact / Event 等 Canonical 概念。

- Ontology 仓库：`github.com/ra1nzzz/YT-Agent-Ontology`（private，PHASE 0 complete）
- **STEPWORK 在其中的身份：`ContentOps` 域 Owner**
- 详见 §4

### P6. 优先复用，禁止重复造轮子

优先级：
1. 本人 `ra1nzzz` 名下自研仓库（含无 License 声明的，均视为自研）
2. MIT 许可的开源仓库 —— 可直接拿来用
3. 授权不允许直接使用的 —— **借鉴其原理与实现方式，重新实现**，但必须在 `REFERENCE.md` 注明灵感来源与方法借鉴

详见 §7 与 `REFERENCE.md`。

---

## 4. 生态位：ContentOps 域 Owner

在 YT-Agent-Ontology 中，STEPWORK 已被登记为 **ContentOps Domain 的 Owner**：

```text
## ContentOps Domain [Owner: STEPWORK]
素材（Material）/ 脚本（Script）/ 视频草稿（Draft）/ 发布（Publish）/ 渠道（Channel）。
挂靠：Material→Artifact；Script→Artifact(document)；发布流程→WorkflowDefinition。
```

### 4.1 必须遵守的 Ontology 契约

| 契约 | 规范 | STEPWORK 现状 | 待办 |
|---|---|---|---|
| **Identity** | `{type}:{namespace}:{name}[-{seq}]`，如 `artifact:yt:script-001` | 用 `_uid("cv")`/`_uid("prj")` 前缀 | 加映射层，不改名（兼容优先） |
| **Event 信封** | `{id, type, actor, subject, timestamp, context, payload}`，`type = domain.action` | `audit_events` 有 `event_type`/`payload` 但非信封格式 | 新增信封封装层（**只加不改**） |
| **Artifact** | `{kind, uri, hash, verification, provenance, attempt}` | `ArtifactEnvelope` 已有 kind/uri/producer，缺 hash/verification | 补字段 |
| **Task/Session** | Task 是工作对象，Session 是事件日志，**禁止混用** | `Job` 语义接近 Task | 加 alias 注释 |
| **Workflow** | Definition（版本化）与 Execution（一次执行）**强制分离** | 无 Workflow 概念（靠前端逐步发命令） | 新增（见 §5） |
| **Skill** | SKILL.md + ZIP 为 Canonical 分发格式；三层模型 | 插件注册表是空壳 | 按 Ontology 重建 |

### 4.2 迁移原则（来自 Ontology，不可违背）

```text
真实产品价值 > 语义统一 > 契约统一 > 接口统一 > 实现统一
迁移永远走：兼容 → 映射 → 迁移 → 删除。禁止大爆炸式重写。
```

**推论**：本次重定位**不重写现有 worker 代码**。所有 Ontology 对齐以「加映射层/加注释/加信封」形式进行，现有功能保持可运行。

### 4.3 待办：补齐 STEPWORK 侧 Ontology 产物

Ontology 仓库已有 `mappings/{orchdesk,ordexa,inpeaknext,proagi,orchclaw}.yaml`，**但没有 stepwork**。需要补：

- `mappings/stepwork.yaml`（机器可读映射）
- `products/stepwork/STEPWORK-ONTOLOGY-MIGRATION.md`
- `prompts/STEPWORK-UNIFICATION-PROMPT.md`

> 注意：STEPWORK 未列入 Ontology 的 PHASE 4-8 迁移排期（当前排的是 OrchClaw/Ordexa/InPeak/ProAGI/OrchDesk）。作为「相邻产品」，需先与母规范侧确认排期与 PHASE 编号。

---

## 5. 目标架构：选题驱动的创作流水线

### 5.1 流水线（核心）

```text
① 发现选题    热点 MCP Server（外部）→ DiscoverHotspots → TopicProposal
② 定角度      GenerateTopic（已有）→ TopicAngle（含 hook/受众/观点/风险）
③ 写文案      GenerateScript（已有）→ ScriptSpec → Scene[]
④ 配音        TTS Provider（stepfun 复刻音色 / edge-tts）
⑤ 配图        Image Provider（新增；可插拔）
⑥ 渲染        Renderer Provider（Playwright 逐帧 + ffmpeg）
⑦ 发布        Publisher Engine（当前为空壳）
```

对应 `JobStage`：`PROPOSING → SCRIPTING → SYNTHESIZING → ILLUSTRATING(新增) → RENDERING → PUBLISHING`

### 5.2 风格层（可插拔）

风格 = **能力需求 + 模板 + 参数**。Orchestrator 按能力需求编排，而不是写死一条流水线：

| 风格 | capabilities | 状态 |
|---|---|---|
| A 纸墨文字版 | `{}` | ✅ 已验证（`gender-video/design.html`） |
| B 插画版 | `{image}` | ✅ 已验证（`design_ai.html` / `design_learn.html`） |
| C 视频素材版 | `{footage}` | 规划 |
| D 数字人版 | `{avatar}` | 规划 |

**A 版零素材依赖 = 天然降级路径**：生图失败/额度耗尽/Provider 停服时，自动回落 A 版照常出片。

美术风格（小黑怪诞/扁平/水彩/像素）与版式风格**正交**，换美术风格不用改模板。

### 5.3 Agent 原生分层

```text
┌─ 外壳层 ────────────────────────────────────┐
│  GUI (Tauri/React)  │  CLI  │  MCP Server   │  ← 三者平等，同一契约
├─────────────────────────────────────────────┤
│  命令总线 bus.py（~90 路由，唯一入口）        │
├─────────────────────────────────────────────┤
│  领域服务：script / voice / image / render   │
├─────────────────────────────────────────────┤
│  出站 Agent：A2A / ACP / MCP Client          │
└─────────────────────────────────────────────┘
```

---

## 6. 真实家底（三分法，别被目录名骗）

### 6.1 ⚠️ 空壳目录（只有 `.gitkeep`）

```text
core/domain  core/commands  core/application  core/events  core/policies
worker/tasks  worker/providers  worker/media
sdk/python  sdk/typescript  sdk/plugin  sdk/agent-adapter
publisher-engine/{browser,dom,rpc,runtime,uploader}
plugins/official  plugins/registry
```

**真实代码全部在 `worker/runtime/`**：120 个 py 文件，16421 行。

### 6.2 ✅ 真实现（可直接依赖）

| 能力 | 落点 |
|---|---|
| 命令总线 | `worker/runtime/commands/bus.py`（`_ROUTES` ~90 条，importlib 懒加载） |
| 任务状态机 | `jobs/engine.py` + `lease.py` + `lifecycle.py`（8 态 × 11 阶段 + heartbeat + 重试 + 取消） |
| Provider 协议 | `providers/{ai,asr,tts,renderer}/base.py`（PEP 544 Protocol） |
| LLM | `providers/ai/cloud.py`（httpx 真实 POST，`complete(prompt, schema)`） |
| 选题/脚本 | `topic/{prompt,parse}.py`、`script/`（含 diff/history/similarity） |
| 品牌档 | `handlers/brand.py` + `migrations/0005`（`brand_profiles` + `brand_reference_scripts` + `RecordPreference`） |
| TTS / ASR | `tts/edge.py`（真 edge-tts）、`asr/whisper.py`（真 faster-whisper） |
| 渲染（弱） | `providers/renderer/ffmpeg.py` —— 仅「纯色背景 + 一行 drawtext」 |
| 字幕/时间线 | `render/subtitles.py`（SRT）、`render/edit_export.py`（OTIO + CMX3600 EDL） |
| 桌面端 | React 18 + Vite 5 + Tauri 2 + zustand；设计系统 `styles/tokens.css` |
| Agent 互操作 | `mcp/server.py`（9 只读工具）、`agents/{mcp_client,a2a_*,acp_client}.py` |
| 契约防漂移 | `results/registry.py` + `scripts/gen_result_types.py` + CI `--check` |

### 6.3 ⛔ 假实现（**别拿它估工期**）

| 位置 | 真相 |
|---|---|
| `providers/asr/local.py` | **硬编码 5 行中文假台词**，按 URI 哈希轮转 |
| `providers/tts/local.py` | **静音 WAV**，只按字数算真实时长 |

不装可选依赖 `.[asr]` / `.[tts]` 时，整条链路会「成功」但产出为空。

### 6.4 ❌ 从零写

文生图（全仓 grep `text2image|dall|flux|sd` 零命中）、Playwright/Chromium 逐帧、热点发现（全仓零命中）、发布引擎。

---

## 7. 可复用资产（优先拿来用）

### 7.1 自研（ra1nzzz）

| 仓库 | License | 可复用点 |
|---|---|---|
| `orchdesk` | 自研（无声明） | 多 Agent 编排桌面工作台（Electron + Cordis/dsh）—— Agent 编排 UX 与插件装配参考 |
| `OrchClaw-Lite` | **MIT** | 项目级多 Agent 协作零依赖 demo —— 可直接借鉴编排模型 |
| `TencentDB-Agent-Memory` | NOASSERTION | Agent 长期记忆 4 层渐进管线 —— 记忆层参考 |
| `douyin-live-info` | NOASSERTION | 抖音直播间信息获取（弹幕/统计/录制）—— **热点发现与平台数据采集参考** |
| `huashu-design` | **MIT** | HTML 原生设计 skill（Agent-agnostic）—— 视觉模板与高保真原型 |
| `model-router` | **MIT** | 按任务类型路由最优模型 —— LLM 成本控制 |
| `harness-agent*` | 自研 | Agent 生命周期 6 阶段管理 —— 流水线阶段治理 |
| `zhiyi-new-agent-onboarding` | 自研 | New Agent 一键入职 —— Agent 注册/发现 |
| `ProAGI` | 自研 | Computer Use / 环境交互 —— 未来平台自动化发布参考 |

> 自研仓库（无 License 声明）视为自有代码，可直接复用。

### 7.2 已验证的自有流水线（本 workspace）

```
C:/Users/my/WorkBuddy/2026-09-07-05-23-14/
├── gender-video/     文字动效版 + 插画版（已出片 4:19）
├── ai-anxiety/       插画版（已出片 3:10）
└── learn-ai-anxiety/ 插画版（已出片 1:44）
```

内含**可直接搬**的资产：`scripts/render.py`（Playwright 逐帧 + ffmpeg 管道）、`gen_tts.py`（stepfun 复刻音色 + atempo 语速归一化 + MD5 缓存判重）、`build_audio.py`（拼接 + timeline）、`design_*.html`（楷体字幕、Ken Burns、同图跨幕不闪烁、末幕分屏特写）。

### 7.3 外部依赖风险

⚠️ **StepFun 生图 `POST /v1/images/generations` 将于 2026-10-10 下线，官方无替代模型**（2026-09-08 查官方文档核实）。

→ 图像层必须可插拔。备选：通义万相 / CogView-4 / 硅基流动 / 本地 SDXL。
→ 设计决策：**配图内不生成中文文字**（生图模型字形不可靠），文字全部在 HTML 层用楷体叠加。

---

## 8. 明确不做

- ❌ 不合并任何仓库（STEPWORK 独立演进，通过 Ontology 与生态互通）
- ❌ 不重写现有 worker 代码（兼容 → 映射 → 迁移 → 删除）
- ❌ 不自建 Workflow Engine / Knowledge Engine（Ontology 原则：消费生态能力，不得自行重新定义）
- ❌ 不追求「理论完整」给 Ontology Core 加实体（域概念放 ContentOps 域）
- ❌ 不做剪映草稿导出（已有 OTIO/EDL，作者已明示）

---

## 9. 归档与链接

旧规划文档已全部移至 `docs/archive/legacy/`，**不再作为执行依据**，仅作历史参考：

| 归档文件 | 原用途 | 现状 |
|---|---|---|
| `PRODUCT_CHARTER.md` | 产品宪章 | **已被本文件取代** |
| `PRD.md` | 需求文档（素材驱动） | 目标已变更，仅历史参考 |
| `STRATEGY_PLAN.md` / `PHASE_PLAN.md` / `MVP_PLAN.md` | 排期 | 被 `ROADMAP.md` 取代 |
| `SYSTEM_SPEC.md` | 系统规格 | 架构部分仍有效，但定位已变 |
| `ROADMAP.md`（旧） | 版本路径 | 被新 `ROADMAP.md` 取代（职责变更） |
| `REFACTOR_PLAN.md` / `UPGRADE.md` / `W*_*.md` | 重构与周计划 | 历史记录 |
| `DECISIONS.md` / `LICENSE_AUDIT.md` / `MIGRATION_ASSESSMENT.md` / `PERF_BASELINE.md` / `settings_page_plan.md` | 各类专项 | 历史记录 |

`docs/adr/`（ADR-001~011）**保留**：Tauri+React、Python sidecar、SQLite WAL、命令总线、Artifact-first 等技术决策仍然有效。

---

## 10. 配套文档

- [`ROADMAP.md`](./ROADMAP.md) — 北极星、在办任务、规划模块
- [`COMPLETED.md`](./COMPLETED.md) — 已完成功能与模块
- [`REFERENCE.md`](./REFERENCE.md) — 引用来源（调研/论文/仓库）
- [`HANDOFF-PROMPT.md`](./HANDOFF-PROMPT.md) — 跨会话开发提示词
- [`archive/legacy/`](./archive/legacy/) — 已归档的旧规划文档
