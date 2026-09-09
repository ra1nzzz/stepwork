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

### S1 · 探路：Playwright 渲染器 ✅ 已完成（2026-09-08）

→ 完整条目移入 [`COMPLETED.md` §1.5](./COMPLETED.md#15-s1--playwright-逐帧渲染器-s1探路-2026-09-08)。
关键事实：30 秒 9:16 成片（1080×1920 / H.264+AAC / 30 fps）实测 `1080×1920 h264 yuvj420p dur=30.000s`；
`progress_cb` 由帧数真驱动；中途取消 `FFmpegCancelled` + `last_proc.poll()` 已回收（0 僵尸）；
原 `FFmpegRenderer` 不受影响；新增 10 条单测 + 1 条 perf 端到端（`pytest -m perf -k real_assets`）；
`mypy strict` / `ruff` / `pytest -m "not perf"` 全绿（685 passed / 7 deselected）。

---

### S2 · 打通：一条流水线端到端 ✅ 已完成（2026-09-09）

**目标**：选题 → 文案 → 配音 → 配图 → 渲染，全链路跑通（插画版）。

**已完成（2026-09-09，S1 衔接部分）**
- [x] `JobStage.ILLUSTRATING = "illustrating"`（`models.py`，只增不改名）
- [x] `migrations/0012_video_scenes.sql` + `.down.sql`（分幕事实表，真往返测试通过）
- [x] `RenderSpec.design_doc_uri`（`background_uri` 降为遗留别名）
- [x] per-request renderer hint（`payload.renderer`，补 P4 缺口）
- [x] `pyproject.toml` 加 `[project.optional-dependencies].render`
- [x] `VideoScene` 模型 + `VideoSceneRepo` + 三命令（`SaveVideoScenes` /
      `ListVideoScenes` / `UpdateVideoScene`）+ CLI `scenes {save,list,update}` +
      schema enum / 前端 union / Agent 白名单同步（表不再空转）
- [x] **文案产出即落幕**（2026-09-09）：`script/segment.py` 确定性切分
      + `jobs.lifecycle.persist_script_scenes`，三条写入路径落版即派生分幕
- [x] **配音步骤**（2026-09-09）：`SynthesizeScenes` 逐幕 TTS + 实测时间轴
      （`audio_uri`/`duration_sec`/累加 `start_sec`）+ 拼整轨；新增 `finish_job`
- [x] **字幕按实测时间轴**（2026-09-09）：`build_srt_from_scenes`，
      等比分配降级为退路
- [x] **配图步骤：接口先于厂商实现**（2026-09-09）：`ImageProvider` 协议
      + `IllustrateScenes`；`local` 占位图非默认值；失败可见且成功幕不回滚
- [x] **渲染侧消费分幕**（2026-09-09）：`RenderSpec.scenes` + `RenderScene`，
      `CreateRenderJob` 把「有实测时长」的幕喂给 PlaywrightRenderer ——
      文本 / 配图 / 起止秒注入视觉稿，画面按幕切换；`born_at_sec` 由
      `window.__getSentBorn` 实测回填。8 条数据流测试 + 1 条 Chromium 测试

**S2 已闭合**：选题→文案→分幕→配音→配图（接口就绪）→按幕渲染，
字幕按实测时间轴。每一段都有「生产端」调用方，无死挂点。

**S3 / 收尾待办**（见 [`COMPLETED.md` §5](./COMPLETED.md#5-文档治理)）：
- 前端分幕 UI（P4 缺口，S6 补）；配图厂商适配器（选型定了再补）；
  `RenderSpec.style_id`/`art_style`/`image_set_id` 真正接入渲染（S3 风格层）

**验收（S2）**
- [x] 字幕与配音对齐（按实测时间轴）
- [x] 单幕可重渲（`video_scenes` 时间轴 + 逐幕配图）
- [x] 生图失败任务进 `FAILED` 且错误可读，不是静默空片

---

### S3 · 风格层：风格可选 + 降级 🔜 进行中

**目标**：版式风格与美术风格正交可选；A 版作为 B 版的降级路径。

**已完成（2026-09-09）**
- [x] **风格注册表 = 能力声明**：新增 `render/styles.py`（`StyleDef`：id /
      label / capabilities / 画面代码）。`ink_text` 纸墨文字版能力集 `set()`
      （零素材，A 版）；`illustration` 插画版能力集 `{"image"}`（B 版）。
      `RenderSpec.style_id` 不再是死字段 —— 渲染前看能力声明就知道需要什么输入
- [x] **内置 A/B 视觉稿**：共享骨架（`__setTime` / `__getSentBorn` /
      幕数学）+ 每风格画面函数拼接成自包含 HTML（file:// 页不能 import 外部
      JS），缓存于系统临时目录（纯函数）。PlaywrightRenderer 文档解析：显式
      文档 > 风格内置稿 > 探路文档兜底；未知风格**当场报错**不静默回退
- [x] **降级不静默**：插画版缺配图（IllustrateScenes 没跑/部分失败）→ 落到
      纸墨文字 A 版出片成功，但 detail 与落库 `video_draft` meta 都带
      `degradedFrom` / `degradedReason` —— 拿到成片的人能看出不是插画版。
      降级链不允许再指向需要图的风格；无降级时 ffmpeg drawtext 路径如实记
      `style_id=None`（不假装用了插画）
- [x] `ListRenderTemplates` 返回 `styles` 清单（能力 + needsImage，前端下拉用）

**待办（2026-09-09 裁决）**
- **`base.html.j2` 不引入 jinja2（YAGNI 裁决）**：现方案「python 常量拼接」已达成
  「骨架单源 + 每风格只覆写画面函数」的目标（shared scaffold + `window._paint`），
  且零新依赖。第三风格出现后若资产组织真的难维护，再抽模板引擎，不提前上框架
- **字体打包（进行中，2026-09-10）**：`resources/fonts/` 已建；`styles.py`
  `font_face_css()` 自动扫描注入 `@font-face`（加字体不用改代码，file:// 绝对
  URI，渲染器已带 allow-file-access）。**已打包：阿里巴巴普惠体 2.0**（55/65/85
  三字重 + 授权 PDF）。待补：阿里妈妈方圆体 VF / 钉钉进步体 / 沐瑶软笔手写体
  （官方渠道手动下载丢进子目录即可）；优设字由棒棒体 / 懒设计字由公益体
  **不打包**（字由客户端专属、禁止转发，许可不允许仓库再分发）
- `image_set_id` 承载配图产物集 id（尚未有表，随厂商选型落）

**验收**
- [x] 同一份 `scenes.json` 能出 A/B 两种片（style_id 切换 → 内置稿切换；
      scenes 注入不变，A 无图、B 有图）
- [ ] 强制生图失败时自动回落 A 版并出片成功（降级逻辑已就绪，等真实浏览器
      出片用例/厂商接入后闭环验收）
- [ ] 字体打包进 `resources/fonts/`（不依赖系统楷体）

**依赖**：S2

---

### S4 · 创作者风格：六维 profile ✅ 已完成（2026-09-09）

**目标**：创作者风格可选，同一选题产出风格可辨识的成片。

**已完成（2026-09-09，后端第一段）**
- [x] `brand_profiles` 扩 `style_dna` 列（`migrations/0013`，真往返测试通过）：
      六维 Creator DNA JSON（`contentStrategy` / `hookDna` / `narrativeDna` /
      `explosionDna` / `languageDna` / `conversionDna`，与 douyin-ego-creator
      蒸馏口径一致；允许扩展键，值须为字符串）
- [x] `CreateBrandProfile` / `UpdateBrandProfile` 支持 `styleDna`
      （畸形拒绝、旧行读出空 dict 兼容）
- [x] `format_brand_prompt_block` 逐维硬约束注入（中文标签，有值才注入）
- [x] **禁用词零出现**（验收断言）：`collect_banned_hits` + `GenerateScript`
      产出命中 → 自动重试一次 → 仍命中则任务 `FAILED` 并**指名命中词**；
      绝不静默放行违规文本、不悄悄改写（改文字 = 改文案语义）。10 条新测试
      （`test_style_dna.py`）
- [x] **风格可辨识验收通过**（2026-09-09 弈韬盲评 2/2 正确）：造两个风格
      强对比 profile（冷峻拆解 / 共情呼喊），同一选题「越努力越焦虑的机制」
      用 StepFun `step-3.7-flash` 真实生成两版，盲标 甲/乙 后人工配对全对。
      素材与驱动：`.workbuddy/s4-acceptance/`（mapping.json 存真实对应）

**待办（收尾）**
- 前端「创作者风格」下拉 → 归 S6；stepfun 复刻音色 TTS provider（§S2 待办）

**验收**
- [x] 切换 profile 后，同一选题的文案风格可被区分（人工盲评 2/2 正确）
- [x] 禁用词（`bannedExpressions`）在生成结果中零出现（断言测试）

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
