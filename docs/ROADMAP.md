# ROADMAP — 北极星、在办任务与规划模块

> **Status:** Active · **Date:** 2026-09-08
> **本文件职责**：记录产品北极星目标、正在执行的任务、规划中的功能或模块。
> **已完成项**请移入 [`COMPLETED.md`](./COMPLETED.md)；**引用来源**请记入 [`REFERENCE.md`](./REFERENCE.md)。
> **旧 ROADMAP** 见 `archive/legacy/ROADMAP.md`（版本路径叙事，已废止）。

---

## 1. 北极星

> **一个人，一条指令，出一條成片。**

量化标准：

| 指标 | 当前 | 目标 |
|---|---|---|
| 端到端耗时（选题 → 可发布成片） | ~40 分钟（人工流水线） | ≤ 15 分钟 |
| 人工决策次数 | 全程人工 | ≤ 2 次（选题确认、成片确认） |
| 风格可迁移性 | 单一风格 | 切换创作者风格后成片风格可辨识 |

约束（不可违背，见 [`REPOSITIONING.md`](./REPOSITIONING.md) §3）：
AGPL 保持 · 热点走独立 MCP Server · Agent 原生双向 · GUI/CLI 一等公民 · Ontology 为准 · 优先复用。

---

## 2. 当前状态

| 项 | 状态 |
|---|---|
| 文档体系重定位 | ✅ 已完成（本文件 + REPOSITIONING/COMPLETED/REFERENCE） |
| 旧文档归档 | ✅ 已完成（22 份 → `archive/legacy/`） |
| 代码改动 | ⏸ **未开始**（现有功能保持可运行） |
| Ontology 侧 stepwork 映射 | ❌ **不存在**（需补 `mappings/stepwork.yaml` 等三份） |

**下一步待启动：S1（Playwright 渲染器探路）**

---

## 3. 路线（S0–S8）

原则：**每阶段独立可验收，回滚 = revert 单个 PR**。不做跨阶段大爆炸。

### S0 · 文档治理与 Ontology 对齐 ✅ 已完成

- [x] 旧规划文档归档到 `docs/archive/legacy/` 并建索引链接
- [x] 新纲领 `REPOSITIONING.md`（短视频创作工厂 · 选题驱动 · Agent 原生）
- [x] 建立 `ROADMAP` / `COMPLETED` / `REFERENCE` 三份职责文档
- [x] 核实 YT-Agent-Ontology 契约（STEPWORK = ContentOps 域 Owner）
- [x] 盘点可复用资产（自研/MIT）
- [ ] ⬅️ **遗留**：向 Ontology 母规范仓库补 `mappings/stepwork.yaml`、`products/stepwork/STEPWORK-ONTOLOGY-MIGRATION.md`、`prompts/STEPWORK-UNIFICATION-PROMPT.md`，并确认 PHASE 编号

---

### S1 · 探路：Playwright 渲染器 🔜 待启动

**目标**：证明「逐帧渲染」能在现有 Job / 进度 / 取消框架里正常工作。

**内容**
- 新增 `worker/runtime/providers/renderer/playwright.py`，实现 `RendererProvider` 协议
  （`render(spec, audio_uri, progress_cb, cancel_event) -> RenderResult`）
- `providers/resolve.py` 加 `resolve_renderer` 的 `STEPWORK_RENDER_PROVIDER=playwright` 分支
- 搬运已验证的渲染脚本：`scripts/render.py`（Playwright 逐帧 + ffmpeg 管道）

**验收**
- [ ] 用现成素材渲出一条 30 秒 9:16 片，1080×1920 / H.264+AAC
- [ ] 进度回调能驱动前端进度条
- [ ] 中途取消无僵尸进程（复用 `render/ffmpeg_runner.py` 的取消语义）
- [ ] 原有 FFmpegRenderer 仍可用（兼容优先，不删除）

**依赖**：无。**这是最高优先级的第一步**——通过后再加东西都是照模式复制。

---

### S2 · 打通：一条流水线端到端

**目标**：选题 → 文案 → 配音 → 配图 → 渲染，全链路跑通（插画版）。

**内容**
- 新增 `worker/runtime/providers/image/{base,stepfun}.py`（照 `ai/base.py` 范式）+ `resolve_image`
- `JobStage` 加 `ILLUSTRATING = "illustrating"`
- 新增 `migrations/0012_video_scenes.sql`：`video_scenes(id, version_id, seq, text, emotion, highlight, audio_uri, image_uri, start_sec, duration_sec)`
- TTS 加 stepfun 复刻音色 provider（含 `atempo` 语速归一化 + MD5 缓存判重）
- `RenderSpec` 扩 `style_id` / `art_style` / `image_set_id`

**验收**
- [ ] 一条 60 秒以上成片，字幕与配音对齐（抽帧检测 ≥ 5 个时间点有字）
- [ ] 单幕可重渲（依赖 `video_scenes` 表）
- [ ] 生图失败时任务进入 `FAILED` 且错误信息可读，不是静默空片

**依赖**：S1

---

### S3 · 风格层：风格可选 + 降级

**目标**：版式风格与美术风格正交可选；A 版作为 B 版的降级路径。

**内容**
- 模板从 `NamedTuple`（背景色/字号/字色）升级为**能力声明**结构（含 `capabilities`）
- 纸墨文字版 A（`set()`）+ 插画版 B（`{image}`）两套模板
- 抽公共 `base.html.j2`（幕号/进度条/`__setTime` 骨架），各风格只覆写画面区与字体
- `fallback_style: ink_text` 配置：生图失败/停服/额度耗尽自动回落 A 版

**验收**
- [ ] 同一份 `scenes.json` 能出 A/B 两种片
- [ ] 强制生图失败时自动回落 A 版并出片成功
- [ ] 字体打包进 `resources/fonts/`（不依赖系统楷体）

**依赖**：S2

---

### S4 · 创作者风格：六维 profile

**目标**：创作者风格可选，同一选题产出风格可辨识的成片。

**内容**
- `brand_profiles` 扩 `style_dna`（结构化六维 JSON）；保留现有字段向后兼容
- `brand_reference_scripts` 承接逐字稿蒸馏原料（如 douyin-ego-creator 的 168 篇）
- `ScriptSpec` / `TopicProposalSpec` 的 `use_brand_profile` 已有，接通六维注入
- 前端加「创作者风格」下拉

**验收**
- [ ] 切换 profile 后，同一选题的文案风格可被区分（人工盲评 ≥ 4/5 正确）
- [ ] 禁用词（`banned_expressions`）在生成结果中零出现（断言测试）

**依赖**：S2（可与 S3 并行）

---

### S5 · 热点发现：独立 MCP Server

**目标**：补齐「选题驱动」的上游入口。

**内容**
- **独立仓库**建 MCP Server（不进 STEPWORK 主仓），连接器：RSS/Atom、GitHub Trending、arXiv/HuggingFace Papers
- 提供 `discover_hotspots` / `list_sources` 等工具
- STEPWORK 侧通过现有 `AddMcpServer` / `CallMcpTool` 接入
- 用现有 `script/similarity.py` 做「新热点 vs 历史选题」去重
- 新增 `DiscoverHotspots` 命令 + `hotspot_items` 表（若需要落库）

**验收**
- [ ] `stepwork-cli` 能列出热点并一键转为 `TopicProposal`
- [ ] 重复选题被相似度过滤拦截
- [ ] 该 MCP Server 可独立安装运行（不依赖 STEPWORK）

**依赖**：S2。**风险**：需求未验证——建议先做最小版验证真伪，再扩展连接器。

---

### S6 · Agent 原生：GUI / CLI 对等 + 出站调用

**目标**：落实 P3/P4——GUI 与 CLI 同为一等公民，双向 Agent 互通。

**内容**
- 补齐 CLI：确保 `stepwork-cli` 覆盖全部 GUI 可达能力（当前 `cli/tests` 仅 58 例）
- MCP Server 工具从 9 个只读扩展为可写子集（**保持不变**：永不暴露 `UpdateConfig`）
- 出站：A2A / ACP / MCP Client 已实现，补端到端联调测试（当前用 `worker/tests/fakes/` 假 Agent）
- 补 `worker/runtime/publish/` 与命令总线的对齐

**验收**
- [ ] 「GUI 能做但 CLI 做不到」的能力数为 0（脚本可枚举校验）
- [ ] MCP 工具清单与命令总线自动同步（扩展 `gen_result_types.py` 机制）
- [ ] 至少 1 个真实外部 Agent 端到端调用成功（非 fake）

**依赖**：S2

---

### S7 · 发布引擎

**目标**：成片能分发到渠道（ContentOps 域的 Publish/Channel 实体）。

**内容**
- 当前 `publisher-engine/` 是**全空壳**（5 个子目录仅 `.gitkeep`），从零建
- 优先复用自研 `ProAGI`（Computer Use / 环境交互）的发布自动化思路
- 已有基础：`handlers/publish.py`（定时发布、平台变体、授权请求、审计）

**验收**
- [ ] 至少 1 个平台打通发布闭环
- [ ] `platform_variants` / `publish_jobs` 表已有，接通

**依赖**：S2。**优先级低于 S3–S6**（发布不是北极星瓶颈）。

---

### S8 · 风格扩展：素材版 / 数字人版

**目标**：验证「加风格 = 加配置，主流程不改」。

**内容**
- C 视频素材版 `capabilities={footage}`（需解决片段时长≠幕时长、版权合规）
- D 数字人版 `capabilities={avatar}`（按分钟计费与排队）

**验收**
- [ ] 新增风格时主流程 diff 为空（只加配置与 provider）

**依赖**：S3（风格层成熟后）

---

## 4. 明确不做

| 不做 | 原因 |
|---|---|
| 合并任何仓库 | Ontology 原则：共享世界模型，不共享代码库 |
| 重写现有 worker 代码 | 兼容 → 映射 → 迁移 → 删除，禁止大爆炸 |
| 自建 Workflow Engine / Knowledge Engine | Ontology 原则：消费生态能力，不得自行重新定义 |
| 热点做进主仓 | 决策 P2：独立 MCP Server |
| 剪映草稿导出 | 已有 OTIO/EDL，作者已明示不做 |
| 接受外部 PR 前不定许可策略 | AGPL 时点约束（P1） |

---

## 5. 排序理由摘要

| 为什么 | 理由 |
|---|---|
| S1 最先 | 验证「逐帧渲染能否在 Job/进度/取消框架里工作」这个最大未知数；通过后其余是照模式复制 |
| S3 早于 S8 | 风格层是 C/D 版的前提，且 A 版是生图停服风险的唯一兜底 |
| S5 独立于主仓 | 决策 P2；需求真伪未验证，独立形态试错成本最低 |
| S7 靠后 | 发布不是「出一條成片」的瓶颈，但 ContentOps 域实体已预留 |
