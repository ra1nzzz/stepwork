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

### S0 · 文档治理与 Ontology 对齐 ✅ 已完成（2026-09-09 全部闭合）

- [x] 旧规划文档归档到 `docs/archive/legacy/` 并建索引链接
- [x] 新纲领 `REPOSITIONING.md`（短视频创作工厂 · 选题驱动 · Agent 原生）
- [x] 建立 `ROADMAP` / `COMPLETED` / `REFERENCE` 三份职责文档
- [x] 核实 YT-Agent-Ontology 契约（STEPWORK = ContentOps 域 Owner）
- [x] 盘点可复用资产（自研/MIT）
- [x] **遗留闭合（2026-09-09）**：向 Ontology 母规范仓库补齐映射三件套，PHASE 编号裁决落到 **D-008**
      - 三件套已入 `YT-Agent-Ontology@b1089e9`：`mappings/stepwork.yaml`（13 实体 local→canonical，`Job → WorkflowExecution` 遵 D-002 不映射 Session）、`products/stepwork/STEPWORK-ONTOLOGY-MIGRATION.md`（13 节路线图）、`prompts/STEPWORK-UNIFICATION-PROMPT.md`（P1/P2 边界 + `to_canonical_event` 接口）
      - **D-008 结论：不为 STEPWORK 新增生态级 PHASE。** 生态 `PHASE 1–12` 与产品 `Phase 0–6` 是两套编号，并存不冲突；映射三件套计为 **PHASE 3（兼容层）** 资产；硬前置是 **ContentOps 域映射基线须在 PHASE 9 前冻结**（已写入母规范 PHASE 8 行）
      - 顺带修了母规范三份既有映射（`inpeaknext` / `orchclaw` / `proagi`）的 YAML 语法：此前声称「机器可读」但 `yaml.safe_load` 直接报错（未加引号的 `": "` 标量、以 `@` 开头的列表项）。六份映射现已全部可解析

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
- [x] **配图厂商适配器**（2026-09-09）：`providers/image/openai_compatible.py`
      —— 一份实现覆盖整份 OpenAI 兼容契约（智谱 CogView-4 / 硅基流动 /
      OpenAI 及任意兼容网关），厂商预置表 + env 覆盖，切厂商 = 改一个 env。
      **通义万相不在契约内**（官方明确不支持 compatible-mode），选它需单开适配器
- [x] **渲染侧消费分幕**（2026-09-09）：`RenderSpec.scenes` + `RenderScene`，
      `CreateRenderJob` 把「有实测时长」的幕喂给 PlaywrightRenderer ——
      文本 / 配图 / 起止秒注入视觉稿，画面按幕切换；`born_at_sec` 由
      `window.__getSentBorn` 实测回填。8 条数据流测试 + 1 条 Chromium 测试

**S2 已闭合**：选题→文案→分幕→配音→配图（接口 + 厂商适配器就绪）→按幕渲染，
字幕按实测时间轴。每一段都有「生产端」调用方，无死挂点。
**仍缺一次真实厂商出片**：适配器已就绪并按厂商文档/复核写死形状，但本机无
生图密钥，尚未跑过真实生图 → 渲染成片（待选厂商 + 配密钥后补验收）。

**S3 / 收尾待办**（见 [`COMPLETED.md` §5](./COMPLETED.md#5-文档治理)）：
- 前端分幕 UI（P4 缺口，S6 补）；
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
- 前端「创作者风格」下拉 → 归 S6
- ~~stepfun 复刻音色 TTS provider~~ → ✅ 已落地（2026-09-09，见 `COMPLETED` 文档治理第 7 条）

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
- [x] `stepwork-cli` 能列出热点（`hotspots sources` / `discover` / `recommend`）
- [x] 重复选题被相似度过滤拦截（推荐打分里的 `novelty` 维，复用 `script/similarity.py`）
- [x] 该 MCP Server 可独立安装运行（不依赖 STEPWORK）
- [x] 热点 → `TopicProposal` 一键转换（**方案 A**：先落一份带出处与信任等级的
      「选题简报」，人/Agent 确认后交给**既有** `GenerateTopic` 消费；
      见下「热点转选题」一节）

**依赖**：S2。**风险**：需求未验证——建议先做最小版验证真伪，再扩展连接器。

**最小验证结果（2026-09-09）** —— 独立仓库 `ra1nzzz/stepwork-hotspot-mcp`（AGPL，零运行时依赖）

- ✅ **通道已通**：STEPWORK 的 `McpStdioClient` 直连该服务，
  `initialize` / `tools/list` / `tools/call` 全通，真实出 12 条热点、0 errors
  （未改 STEPWORK 一行代码）
- ✅ **能稳定拿到的源**（全部免密钥，实测 11 源 60 条 **0 errors**）：
  **抖音热榜**（官方，50 条中文热榜词 + 热度值）、**今日头条热榜**（官方，
  50 条热点 + HotValue）、**NewsNow 五榜**（微博/知乎/头条/百度/B站 ——
  微博百度官方接口都要 cookie/风控，走开源聚合是唯一低成本绕法）、
  InfoQ + 少数派 RSS（中文科技/AI）、arXiv、HuggingFace、GitHub Trending
- ⚠️ **第一版结论曾误判「中文热搜拿不到」**，已翻案（2026-09-10）：那是
  **探测方法问题** —— 用第三方镜像当「微博热搜」的代表、没跟 http→https
  重定向（把 arXiv 判死）、用默认客户端 UA 触发 WAF（InfoQ 451）。
  **教训：判源死活要先分清「对方不给你」和「你没敲对门」**
- ❌ **真的拿不到**（性质是对方侧）：RSSHub 公共实例（Cloudflare 403，需自建）、
  36氪 `/feed`（端点已改版成 HTML 页，不再是 RSS）、微博热搜（官方接口要 cookie）
- ✅ **热点宝已接上（2026-09-10，弈韬裁决「可以接上」）**：走 CDP 复用用户
  已登录的浏览器（`--remote-debugging-port`）。**不伪造 a_bogus 签名**（那是绕
  风控），只读（goto + 读响应体/可见文本），不碰密码、不存 cookie、不复制 profile。
  playwright 列 `[browser]` **可选**依赖，不破坏上游「零运行时依赖」的立身之本
- ✅ **热点筛选与推荐机制已落地（2026-09-10）**：弈韬要求「产品本身是 Agent，
  需要有热点筛选和推荐机制」—— 见下「推荐机制」一节
- ✅ **热点 → 选题已打通（2026-09-10，方案 A）**：`ConvertHotspotToTopic` 落一份
  带出处与信任等级的「选题简报」，确认后由**既有** `GenerateTopic` 消费 ——
  见下「热点转选题」一节
- ✅ **CLI `mcp` 子命令已补（2026-09-12）**：登记 / 看工具 / 调用三个入口
  （`mcp add|tools|call`），S6 的 GUI/CLI 对等缺口收窄一格 —— 详见 S6 一节
- ✅ **前端热点面板已落地（2026-09-12）**：判据层 + 面板挂在「02 原创角度」，
  六条产品红线（降级可见 / 退化提示 / 待复核标 / 空态解释 / 乘性口径 / next_step 键名）
  由 23 例单测钉住 —— 详见 S6 一节

**抖音热点：三条路径的调研结论（2026-09-10）**

| 路径 | 可行性 | 说明 |
|---|---|---|
| ① 抖音热榜官方 web 接口 | ✅ **已落地** | `iesdouyin.com/web/api/v2/hotsearch/billboard/word/`，50 条热榜词 + 热度值，免登录免密钥 |
| ② 热点宝 `douhot.douyin.com`（浏览器自动化） | 🟡 需登录态 | 价值最高：10 大榜单（含低粉爆款/高涨粉率）、200+ 垂类、热词 60 日趋势、官方活动日历。**没有公开 API**，只能驱动已登录浏览器取；需 Playwright 连真实浏览器配置 + 首次扫码 |
| ③ 抖音开放平台 API | ❌ 个人不可行 | 需**企业/个体工商户资质**，个人开发者无法申请；且开放能力给的是「视频/粉丝数据（T+1）」，**热榜不在公开能力清单里** |

**推荐机制（2026-09-10 落地）** —— 弈韬的原话是「产品本身是 Agent，需要有热点
筛选和推荐机制」。所以这里刻意**不做「返回一个列表」**：列表是源的事，推荐的
事是**下判断并解释判断**。

架构切分（`worker/runtime/hotspot/`）：

| 模块 | 职责 | 为什么这样切 |
|---|---|---|
| `mcp.py` | 与上游 MCP 对接（找连接 / 发 JSON-RPC / 解回包） | 热点源形态变化快，本仓不内联任何抓取逻辑（P2） |
| `rank.py` | **纯函数**打分与排序 | 推荐错了要能复盘；掺进 IO 就只能「跑一遍看」 |
| `models.py` | 落库事实形状 + 打分分解 + 推荐项 | 与上游 JSON 解耦，上游改字段只改一个 `from_mcp` |

总分 = `core × gate × (1 − penalty) × 100`。**品牌契合是闸门而不是加权项** ——
见下方「验收打出来的两处修正」。

| 维度 | 权重 | 口径 |
|---|---|---|
| `freshness` | 0.30 | 半衰期 24h 衰减；**源没给时间取 0.5 中性**，不当坏信号 |
| `heat` | 0.20 | **源内分位**，不是原始 score（抖音千万级 vs GitHub star 千级不可比） |
| `novelty` | 0.10 | 与历史选题的最大相似度取反（复用 `script/similarity.py`） |
| `feedback` | 0.10 | adopted 0.2（做过了）/ ignored 0.35 / rejected 0.0 / 无反馈 0.5 |

（以上四维在 `core` 内部归一化。）品牌契合单独作闸门：
`gate = 0.20 + 0.80 × brandFit`；命中**禁用表达**是**乘性惩罚**（总分归零）。

四条纪律写进了测试，别退化：

1. **热度只源内比**（`test_cross_source_heat_does_not_leak`）
2. **缺信息取中性**，否则榜单类源会被整体压死（`test_freshness_decays_and_is_neutral_without_time`）
3. **品牌契合是闸门**（`test_irrelevant_hotspot_cannot_outrank_a_relevant_one`）
4. **惩罚乘性**（`test_penalty_is_multiplicative_not_additive`）

### 真机验收打出来的两处修正（2026-09-10）

第一次跑真实热点（40 条，含社会新闻）时推荐结果**不可用**，两处设计被推翻：

| 症状 | 根因 | 修正 |
|---|---|---|
| 「习近平对青岛货轮火灾作出指示」并列**第一**（65 分），对一个「AI 工具与效率方法」账号毫无意义 | 品牌契合只当 0.30 加权项，被时效 1.0 + 热度 1.0 + 新颖 1.0 + 反馈 0.5 凑出的 0.65 **压过去了** | 改为**闸门**（下限 0.20）：无关内容最高只能拿 20 分。修正后该条 65 → 18.6，前 4 名全是 AI 相关（28.9 / 25.2 / 25.2 / 19.7） |
| 品牌契合普遍 `0.00–0.08`，闸门把所有人都压低 | 分词把**跨中英边界的 2-gram**（「的a」「i工」）与**长句整串**（定位语 26 字）都塞进关键词表，分母被撑到 40+，真实命中被稀释 | 清噪两条：跨语言 2-gram 不入表；超 6 字的串不整体入表。另给**内容支柱**额外加权（用户手选的取向声明，命中一个顶三个普通词）。同一批标题下 0.08 → 0.24/0.92 |

**已知局限（字面匹配的固有边界）**：「如何评价霍奇猜想疑似被 **OpenAI** 解决」
契合度 0.00 —— 英文 token 是 `openai`，切不出 `ai`。这类要靠 AI 写理由那层补语义
判断，纯规则打分做不到。**不做子串匹配**（`openai` 含 `ai` 会误伤 `said`）。

理由：**AI 写一句「为什么值得做」，未配 AI 或调用失败时降级为规则模板**，
且 `reasonSource` 如实写 `rule` —— 降级必须可见，不能让 UI 把模板句当 AI 洞见。

反馈闭环：`RecordHotspotFeedback`（adopted/ignored/rejected）→ 下一轮 `feedback`
维生效。**同（热点, 工作区）覆盖而非追加**：一条热点只能有一个结论。

---

### 热点转选题：方案 A（2026-09-10，弈韬裁决）

热点是**外部未核实**内容（PRD-AGT-003），不能冒充已验证素材直接产出选题。
所以中间隔一步 `ConvertHotspotToTopic`，把「外部素材」和「下判断」分开：

| 步骤 | 命令 | 做什么 | 调 AI |
|---|---|---|---|
| 1 | `RecommendHotspots` | 打分排序 + 写理由 | 仅写理由时 |
| 2 | `ConvertHotspotToTopic` | 把一条热点装订成「选题简报」落 `content_versions` | 否（事实拼装） |
| 3 | `GenerateTopic` | **既有命令，零改动**：读简报正文 → 出 angles | 是 |

关键设计（每条都在测试里钉住）：

- **简报不是选题**：它是素材包（标题 / 来源 / 链接 / 热度 / 摘要，外加可选的
  推荐理由与打分分解），**不含 `angles`**。真正下判断留给 `GenerateTopic`。
- **免责头写在正文里**，不是只写进 `producer`：下游读的就是 `content` 文本，
  把限制写在这里，AI 提示词、人工阅读、导出都躲不开。
- **来源与信任等级落 `producer`**：`trustLevel=external-unverified` +
  `reviewState=pending_review`，复用 `agents/channel.py` 的同一套常量 ——
  与出站 Agent 产物共用词表，UI 才能用一套逻辑筛出全部待复核产物。
- **理由 / 打分分解由调用方回传，转换命令不重算也不猜**。它们是
  `RecommendHotspots` 那一刻的判断结果，不是热点事实（发现取事实、推荐下判断）。
  没带就如实写 `reason_source="none"`，**绝不编一句听起来合理的理由**。
- **同输入重转按内容哈希复用**：Agent 会重试，不该刷出版本洪水；但理由变了
  就是新版本（复用不能过头）。
- 简报的 `content_type='hotspot_brief'` 不在 `load_topic_history` 的取数范围，
  不会被「重复选题提醒」当成历史选题比对。

⚠️ **`next_step.payload` 用的是 snake_case**（`source_version_id`）：
`GenerateTopic` 的 `TopicProposalSpec` 直接吃 payload、没有 camelCase 别名，
写成 `sourceVersionId` 会当场 `INVALID_ARGUMENT`。本域其余命令
（`hotspotId` / `reasonTopN`）是 camelCase —— **两套约定并存是既成事实**，
照 `next_step` 抄才对。

⚠️ **没配品牌档时推荐会退化**：`brandFit` 取中性 0.5，闸门等于不设防，排序基本
退化成「热 + 新」（CLI 实测：无品牌档时榜首是「青岛货轮火灾25人遇难」）。这本身
**是正确行为**（没有画像就无从判断契合），但**热点面板应当提示「未绑定品牌档、
推荐质量受限」**，否则用户会以为这就是推荐机制的水平。前端部分留 S6。

**真机验收（2026-09-10）**：在真实抓取的 40 条热点上跑 推荐 → 转换 → `GenerateTopic`，
**PASS**：简报正文确实进了提示词、`parent_version_id` 挂在简报上、重转返回
`reused=true` 且版本数不变。报告在 `.workbuddy/hotspot-acceptance/convert-report.md`。
CLI 侧同样验过（`hotspots convert --hotspot-id … --reason … --breakdown-json …`）。

**已知局限**：本机未配任何 AI Provider key，验收里的 `GenerateTopic` 用的是
**记录提示词的替身 AI** —— 它证接线，不证模型产出质量。

---

### S6 · Agent 原生：GUI / CLI 对等 + 出站调用

**目标**：落实 P3/P4——GUI 与 CLI 同为一等公民，双向 Agent 互通。

**内容**
- 补齐 CLI：确保 `stepwork-cli` 覆盖全部 GUI 可达能力（当前 `cli/tests` 仅 58 例）
- MCP Server 工具从 9 个只读扩展为可写子集（**保持不变**：永不暴露 `UpdateConfig`）
- 出站：A2A / ACP / MCP Client 已实现，补端到端联调测试（当前用 `worker/tests/fakes/` 假 Agent）
- 补 `worker/runtime/publish/` 与命令总线的对齐

**验收**
- [x] 可枚举校验脚本就位：`scripts/check_ui_parity.py`（进 CI）
- [x] 「GUI 能做但 CLI **到不了**」的能力数**归零** —— 通用逃生舱 `call` 覆盖全部 98 条路由，实测 **0 条**
- [x] 「GUI 能做但 CLI 没有**专门子命令**」的能力数归零（P4 一等公民）—— 实测 **0 条**（原 36 → 0）
- [x] MCP 工具清单与命令总线自动同步 —— 落地为 `scripts/check_mcp_surface.py`（进 CI），
      8 项检查全绿、6 条护栏做过负向验证。比原计划的「扩展 `gen_result_types.py` 机制」
      更进一步：那份机制管的是**生成物同步**，这里管的是**跨对象自洽**（详见下方第五批）
- [x] 至少 1 个真实外部 Agent 端到端调用成功（非 fake）—— `mcp/tests/test_mcp_e2e.py`
      进 CI；真机探针两个 `STEPWORK_HOME` 对照跑（详见下方第六批）

> **为什么拆成两条**：「能不能做到」和「好不好用」是两个问题。达标判据是前者
> （GUI 能做的 CLI 必须有办法做到），但把两者混成一个数字，会被读成「已经做完了」。
> 门禁因此分 C1（可达性）/ C2（专门子命令）两层 —— **现已双零，都是硬失败**，
> 不再有冻结基线可躲。

**已推进（2026-09-12）：CLI `mcp` 子命令**

补上 `AddMcpServer` / `ListMcpTools` / `CallMcpTool` 的 CLI 入口：

```
stepwork-cli mcp add   --command "python -m stepwork_hotspot_mcp.server" [--name …]
stepwork-cli mcp tools --connection-id <id>
stepwork-cli mcp call  --connection-id <id> --tool list_sources [--args-json '{"k":1}']
```

这一项的由来是**验收时被自己绊到**：S5 登记热点 MCP 只能靠临时脚本直接调
`AddMcpServer`，因为 CLI **根本没有 mcp 入口** —— GUI/CLI 不对等不是纸面缺口，
是当场要绕路。

踩到一个 `dest` 冲突值得记：`mcp add --command` 的 `dest` 默认就是 `command`，
而顶层 `args.command` 存的是**子命令名**（`cli/__main__.py` 的 `build_payload`
靠它路由）→ 被覆盖后报 `unknown command: 'python -m my_server'`。
改 `dest="server_command"` 解决。**新增子命令参数前，先确认 `dest` 不撞顶层
`command` / 子命令组自己的 `dest`**。

真机验收 PASS（真实子进程 + 真实 stdio MCP Server，报告
`.workbuddy/hotspot-acceptance/mcp-cli-report.md`）：`mcp add` 探测到
`server_info={"name":"stepwork-hotspot-mcp","version":"0.1.0"}` 与 3 个工具；
`mcp call --tool list_sources` 回落 gzip 后 2007 字符 / 13 个源，
`is_error=false`、`trust_level=external-unverified`、`review_state=pending_review`；
错误路径退出码 1 且带 `MCP_CLIENT_RPC_ERROR: unknown tool: nope`。

**已推进（2026-09-12 · 第二批）：对等门禁 + 前端热点面板**

*1. 先把「验收」本身做成可执行的 —— `scripts/check_ui_parity.py`（进 CI）*

不靠人工维护一张能力清单（那种清单一定会和代码脱节），直接解析三面事实：
权威路由（`bus.py` 的 `_ROUTES`）、CLI 入口（`cli/__main__.py` 的
`set_defaults(command_type=…)`）、GUI **真实调用点**（`buildEnvelope` /
`runCommand` / `useCommand` 的字面量首参，并解开
`const commandType = a ? "X" : "Y"` 这层别名）。另加一项 detail 字段消费清单：
契约里登记的字段在前端有没有真实读点。

它当场推翻了一个乐观假设：缺口**不是 1 条（就差个 mcp），是 38 条** —— 插件、
Agent 连接、审批、定时发布、品牌脚本、项目导入导出整片能力只有 GUI 路径。
所以 C 项不能直接硬失败（会红到今天不能进 CI），改为**冻结基线**：
`_KNOWN_GAP` 记下 38 条，**只许减不许增**。补一个删一个，删空即本条验收达成。

顺带修掉脚本自己一个假报告：动态调用点的正则原写成「首参不是引号」
（`\(\s*(?!")`），而 `\s*` 可以零宽匹配 —— 于是**每一处跨行的字面量调用**
都被误报成「解析不了」。改成「首参是标识符」后收敛到真实的 2 处。

*2. 前端热点面板 —— 判据先落成纯函数，UI 只做渲染*

- `features/hotspots/viewModel.ts`：契约读取（顶层 snake_case / 条目 camelCase
  **只在这一处**读，键名写错不会报错只会静默 undefined）+ 六条产品红线的判据：
  理由降级可见（`rule` 绝不能当 AI 洞见）、品牌闸门退化提示、外部素材「待复核」标、
  空态给解释（没数据 ≠ 被筛掉）、打分口径不得展示成加总（乘性公式）、
  下一步 payload 键名直接取自契约（`source_version_id`，不让 UI 手写）
- `features/hotspots/HotspotPanel.tsx`：抓取 → 推荐 → 转简报 → 交棒角度。
  挂在「02 原创角度」而不是新开一级导航 —— 侧栏 7 项是 PRD Ch.7 定的，而两条
  起手线（素材分析 / 热点推荐）的产物都是「一个 `content_version`」，挂在**消费
  来源的地方**最自然；简报落库后写 `sourceVersionId`，既有 `GenerateTopic` 流程
  零改动复用
- `lib/useCommand.ts` 新增 `describeCommandError()`：`errorText` 只给错误码，
  但可操作信息在 `detail` 里（外部 MCP Server 的 `message` / `stderr`）——
  后端早有 `agents/mcp_client.describe_error()` 做同一件事，前端这次对齐

*3. 判据层测试 23 例（夹具取自真机 CLI 输出，不手搓）*

**前提是先把前端依赖装上**：`apps/desktop` 此前 `node_modules` 为空，
`npm ci` 后前端测试才第一次真正跑起来（`vitest` 81 passed）。这一条本身就是
「CI 绿 ≠ 本地可验」的提醒 —— 依赖没装时 `npm test` 根本到不了断言。

**已推进（2026-09-12 · 第三批）：CLI 通用逃生舱 `call` —— 可达性归零**

上批把缺口量出来了（38 条），这批先还掉**可达性**那一半：

```
stepwork-cli call <CommandType> [--payload-json '{"k":1}']
stepwork-cli call --list          # 打印权威路由表（JSON 数组）
```

一条子命令让**全部 98 条路由**从 CLI 可达 → 门禁 C1 从「欠 38」直接**归零**。
刻意不逐条补专门子命令再收工：**可达性 ≠ 手感**。补 36 个专门子命令是几周的量，
而「CLI 到不了」这个洞只要一条子命令就能填上；把两者拆开，洞当天就补，手感慢慢做。

几个刻意的设计：

- **不给默认值**：`--payload-json` 缺省即空对象 `{}`，不替目标命令猜参数
  （目标命令自己才是它的权威解释者）。键名也**原样透传不做翻译** ——
  契约里 payload 键名本就不统一（`source_version_id` vs `hotspotId`），
  在 CLI 里"顺手统一"反而会造出第二套口径。
- **`--list` 打的是后端路由表**，不是 CLI 维护的第二份清单。那种副本一定会脱节
  —— 本脚本第一版就因为漏看 `cli/config.py` 报了两条假缺口，同一个教训。
- **未知命令在 CLI 就拦下**，带 `difflib` 近似建议：
  `unknown command_type: ListPlugin；你是不是想用 ListPlugins / …`
  （下发后再拿 `unknown commandType` 对用户毫无帮助）。
- **一小撮命令拒绝走逃生舱**：`_CALL_DENY` 只登记 `UpdateConfig` —— 它有带安全
  设计的专门入口（`config set --file/--stdin`，密钥不进 argv）。逃生舱补的是**缺口**，
  不该顺手把已有的安全约束绕掉。

同日还修掉对等脚本自身两处问题：

| 问题 | 现象 | 修法 |
|---|---|---|
| CLI 面只看 `__main__.py` | `GetConfig`/`UpdateConfig` 住在 `cli/config.py`，被误报为缺口 | `_cli_commands()` 扫**整个 `cli/` 包** |
| 逃生舱探测正则太宽 | `mcp call` 也有 `add_parser("call")`，删掉真货也报「有兜底」 | 锚到顶层 `\bsub\.add_parser\(` |

**注意**：C2 的 36 条里，`GetConfig`/`UpdateConfig` 两条是**修正误报**还掉的，
不是真补子命令 —— 别把这 2 条算成进度。

**已推进（2026-09-12 · 第四批）：逐域补齐 36 个专门子命令 —— 一等公民缺口归零**

上一批用 `call` 把**可达性**补到 0；这批把**手感**补到 0，CLI 入口从 47 涨到 **83**。

新增 7 个组、扩展 5 个组，共 36 个专门子命令：

| 域 | 子命令 |
|---|---|
| `plugin`（新） | `list` `preview --path` `install --path` `uninstall --id` `enable --id` `disable --id` `health --id` |
| `agent`（新） | `connections` `tasks` `artifacts` `set-status --id --status` `disconnect --id` |
| `a2a`（新） | `add --url [--token]` `start [--port]` `stop` `status` |
| `acp`（新） | `add --command` |
| `approvals`（新） | `list [--status] [--limit]` `decide --id --decision` |
| `diagnostics`（新） | `export [--desensitize/--no-desensitize] [--max-log-lines]` |
| `provenance`（新） | `get --subject-type --subject-id` |
| `publish`（扩） | `timeline` `fill` `auth-request` `schedule` `unschedule` `schedules` `fire-due` |
| `brand`（扩） | `update` `scripts` `script-import` `script-delete` |
| `project`（扩） | `export` `import` |
| `assets`（扩） | `delete` |
| `versions`（扩） | `diff` |

补齐过程中值得记的三点：

1. **键名一律回读 handler 再写**，不凭印象。handler 普遍写成
   `p.get("a") or p.get("b")`（兼容两种命名），这带来一个**无声失败模式**：
   键名写错既不报错也不生效，命令成功、字段静默为默认值。所以测试用一张表
   断言**完整 payload 字面量**（47 条），而不是只断言 `commandType`。
2. **`acp add --command` 的 `dest` 必须绕开顶层 `command`** ——
   `mcp add` 在这里踩过一次（子命令名被启动命令覆盖 → `unknown command`）。
   已单独留一条测试钉住。
3. **逃生舱与专门入口不是二选一**：`call` 保留，专门子命令照顾常用路径。
   门禁 C2 现在**基线清空（`_KNOWN_GAP` 为空集合）**，语义从「允许还债、
   禁止添债」收紧成纯粹的**禁止添债** —— 今后 GUI 新增能力而 CLI 没跟会当场红。
   已用负向用例验证过护栏确实会响（临时拆掉一个映射 → exit 1 并点名）。

**已推进（2026-09-12 · 第五批）：MCP 工具面一致性门禁 —— 「根授权保证」从注释变成事实**

MCP 是第四个入口，但它比 CLI / GUI 多担一条**安全边界**：只暴露只读工具，且
`update_config` / `UpdateConfig` **永不注册**（密钥不可能经 MCP 写入）。这条保证
此前只写在 `mcp/server.py` 的模块 docstring 与注释里 —— 那是**承诺**：有人加一个
`update_config` 工具，注释一个字都不会变，没有任何东西会响。

新增 `scripts/check_mcp_surface.py`（进 CI，紧挨 `check_ui_parity.py`）。它不认人工
清单，用 `ast` 解析五处真实定义（MCP 的 `_TOOL_COMMANDS` / `TOOLS` / `_build_payload`；
bus 的 `_ROUTES` / `_AGENT_ALLOWED_COMMANDS` / `_AGENT_SOURCES` / `_AGENT_ACTOR_TYPES`
/ `_ALLOWED_CONFIG_ACTORS`），做 8 项检查：

| 检查 | 含义 |
|---|---|
| A | 六处定义都解析出了东西 —— 解析失效必须 exit 2 报明白，绝不静默变绿 |
| B | MCP 指向的命令必须真实存在于 `_ROUTES`（拼写错必死） |
| C | `TOOLS` 与 `_TOOL_COMMANDS` 必须是**同一批名字**（双向） |
| D | MCP 暴露的命令必须在 bus 的 agent 只读白名单内 |
| E | `update_config` / `UpdateConfig` 在 MCP 面与配置白名单里永不出现 |
| F | `inputSchema` 自洽：`type: object` / `required ⊆ properties` / 每个属性有 `type` |
| G | 声明的 property 必须真被 `_build_payload` 读（否则 Agent 传了被静默丢弃） |
| H | `_build_payload` 读的键必须被声明过（否则 Agent 根本没法提供） |

三点值得记：

1. **C 是最有价值的一条**，因为它挡的是一种「不报错、只让人迷惑」的坏法：只写
   `_TOOL_COMMANDS` → 工具存在但 `tools/list` 里看不到；只写 `TOOLS` → 广告了一个
   「调用即 `unknown tool`」的工具。两个方向都不会自己报错。
2. **顺手清掉三处手写副本**（漂移的根源）：模块 docstring 里枚举的 9 个命令名、
   `list_tools()` docstring 里的 `exactly 9 tools`、以及测试里重复的工具名清单。
   第二份副本就是第二个会忘记同步的地方 —— 全部改成指向 `_TOOL_COMMANDS`；测试里
   保留的那份**换了个身份**：不再是清单副本，而是**安全边界的变更检测器**（扩充 MCP
   工具面必须是刻意动作，必须在测试里显式改一行）。
3. **G / H 只能做函数级判定，逐工具的精确版放进测试**：`_build_payload` 是一条 if
   链，静态切分支会因 fall-through 出假报告，而假报告会让人不再信任这道门禁。于是
   `mcp/tests/test_mcp.py` 里用探针值**逐个工具真跑一次** `_build_payload`，断言每个
   声明的参数都真的落进了 payload。

**负向验证当场抓到自己的一个真漏洞**：把 `_TOOL_COMMANDS` 改名后，脚本并非按设计
打印 `FAIL A` 并 exit 2，而是**抛 traceback 退出 1**。检查项 A 的全部意义就是「解析
坏了要说人话」—— traceback 会被读成「脚本自己坏了」，而且不告诉你要改哪个对象。
改为逐对象解析、一次列全所有坏掉的对象。六条护栏（A / B / C / E / F / G）逐一验证
会响且点名正确，收尾复跑确认 `mcp/server.py` 已还原。

**已推进（2026-09-12 · 第六批）：真实外部 Agent 端到端 —— S6 收口**

S6 验收清单最后一条。此前「出站」那条已真机验过（STEPWORK 当 client 连热点 MCP
Server，13 个源）；这条是**入站**：外部 Agent 当 client，连 STEPWORK 的 `mcp/server.py`。

落地为 `mcp/tests/test_mcp_e2e.py`（进 CI）。「非 fake」逐条可核对，一层替身都没有：

| 环节 | 真实的东西 |
|---|---|
| 客户端 | `worker/runtime/agents/mcp_client.py` 的 `McpStdioClient`（生产代码） |
| 传输 | stdio + 行分隔 JSON-RPC 2.0，**真子进程**（不是 in-process 直调） |
| 服务端 | `python -m mcp.server`（生产代码） |
| 后端 | 真实 SQLite + 真实 migration + 真实 dispatch → 真实 handler |
| 数据 | 由**父进程**写入、由**子进程**读回 |

**「非 fake」需要一个可证伪的判据**，否则「端到端通过」只是自我声明。本轮的判据是
**同码对照** —— 同一条命令、同一份代码，只换 `STEPWORK_HOME`：

```
有种子数据的 home → list_projects 返回 1 个项目（标题/版本/全文逐字段一致）
空 home           → 同一条命令返回 []
```

两次结果不同 ⇒ 数据来自**子进程自己打开的那个库**。若两端共用任何 in-process 对象，
两次必然返回同一个结果，这个对照会当场拆穿。真机探针
（`.workbuddy/mcp-acceptance/probe_inbound_e2e.py`，报告同目录）12 条判据全绿，
含「服务端子进程 PID ≠ 父进程 PID」与「`update_config` 被拒 `unknown tool`」。

> 与 `worker/tests/fakes/` 的分工要写清楚：那些替身用来**隔离**某一层（不装 AI
> provider 也能测 handler），是必要的；本条验收要证明的是**各层接起来真的通**，
> 所以一层替身都不许有。同理，`test_mcp.py` 里 monkeypatch `run_command` 的隔离
> 测法在本文件是**故意不用**的 —— 它证明信封构造，不证明后端接得通。

**顺带补掉 lint / type 的覆盖盲区**：CI 原先 ruff 与 mypy **都**带
`working-directory: worker`，于是 `scripts/`（两道门禁本体）、`cli/`（CLI 入口）、
`mcp/`（MCP 服务端）**全在覆盖之外 —— 都是生产代码却没人看**。改为从仓库根
`ruff check .`（根目录只有一份 pyproject.toml，规则与在 `worker/` 内跑一致），并新增
`mypy cli mcp scripts`。mypy 目标写**显式目录**而非 `.`：mypy 不读 `.gitignore`，
`.` 会把本地 `.workbuddy/` 验收探针也拉进来，而那些文件 CI 里根本不存在 →
「本地红、CI 绿」是最难查的一类不一致。

**已推进（2026-09-13 · 前端数据层的死挂点与形状断言）**

起因是补 S7 前端入口时顺手 grep 了一下「有没有人用 `useCommand`」，结果是零。查下去发现
这不是「组件忘了用」，而是一个**结构性死挂点**：

| 事实 | 判据 | 结论 |
|---|---|---|
| react-query 已接入 | `main.tsx` 有 `QueryClientProvider` + `new QueryClient` | 不是「用不了」，是「没人用」 |
| hooks 零调用 | `grep -rn "useCommand" src --include=*.ts* \| grep -v lib/useCommand` → 空 | `useCommand` / `useCommandMutation` 是死代码 |
| `runCommand` 仅 3 处 | `AgentView` / `HotspotPanel` / `PublishView` | 而这三个文件**同时**还在手写 `dispatchCommand` |
| 手写风格仍是主流 | 72 处 | 旧写法没被清除，新写法没被采纳 |

**要害在 docstring 与现状脱节**：`useCommand.ts` 写着「不可能『忘了检查』，因为根本没有
『检查』这个步骤可以忘」—— 那是**这套写法成立时才有的性质**，而现实中 72 处各自手写、
各自记着检查。注释描述的是意图，不是现状；新人照它理解，会以为这层已经收口了。

**先量化再动手**：写探针扫「拿到 `dispatchCommand` 返回值却没读 `.ok`」的调用点，初判 7 处可疑。
逐条复核后 **7 处全是误报** —— `Promise.all` 数组解构（`assetRes.ok` 在 14 行外）、
函数定义行、跨函数委派（`bridgeConfig` → `adaptConfig` 内有 `if (!res.ok)`）。
即：**当前不存在真实缺陷**，静默失败出口是被各处手写补齐堵住的。

因此**不迁移**那 72 处（改 20+ 文件的风险大于收益），改为把这个性质**变成可执行的断言** ——
`apps/desktop/src/lib/commandUsage.test.ts`：

- **检测器自检**：喂合成源码（漏检 / 已检 / `Promise.all` 漏一个 / 直接 return / 丢弃 / 豁免标记）
  断言检测器逐条判对。光有「跑一遍是绿的」证明不了护栏有效 —— 它可能什么都没匹配上
- **全仓断言 0 违规**：72 个调用点必须都读过 `.ok`，否则测试红并**点名**文件与行号
- **覆盖面下限**：源码里 `dispatchCommand(` 出现次数 ≥ 60（实测 76），防止大删之后这条
  测试变成「空转的绿」
- **豁免要写出来**：白名单只有 `lib/tauri.ts`（通信层，原样交出结果）与 `lib/useCommand.ts`
  （全仓唯一集中检查点），每项必须写理由；行内豁免须写 `// ok-check: <理由>`

**负向验证**（真实源码，不是合成输入）：把 `ProjectDetailView` 的 `if (!proj.ok)` 改成
`if (!proj.commandId)` → 测试红，报 `features/projects/ProjectDetailView.tsx:76  \`proj\` 从未读过 \`.ok\``，
**失败仅 1 条**（没有连带炸一串），退出码 1；还原后复绿、`git diff` 为空。

同时**改注释说真话**：`useCommand.ts` 的文件头补上现状（3 处 / 0 处 / 72 处）与一句可操作提醒 ——
迁移完成前，照抄邻近文件的手写写法仍是这一层的现实默认，**复制粘贴时请连 `if (!res.ok)` 一起复制**。

**依赖**：S2

---

### S7 · 发布引擎

**目标**：成片能分发到渠道（ContentOps 域的 Publish/Channel 实体）。

**内容**
- 当前 `publisher-engine/` 是**全空壳**（5 个子目录仅 `.gitkeep`），从零建
- **底座候选已定：[OpenCLI](https://github.com/jackwener/opencli)（Apache-2.0）** ——
  见 `docs/adr/ADR-012-opencli-absorption.md`。它有 `douyin`（含 `draft`/`drafts`）、
  `xiaohongshu`、`weibo`、`bilibili` 的现成适配器，且许可证与 AGPL 兼容
  （对比：`douyin-live-info` 是 CC BY-NC，只能借鉴原理）
- ⛔ **只走 fill / draft 路径**：ADR-008 规定 V0.x 只允许 FILL_AND_PREVIEW，
  「最终点击发布必须由用户手动完成」→ **禁用 OpenCLI 的 `publish` 命令**
- 接入形态照 S2 的 `ImageProvider` 先例：**接口先于实现**，列 `[publish-opencli]`
  可选依赖；未装 / daemon 未起 / 未登录一律显式 `UNAVAILABLE` / `NEED_LOGIN`，不静默降级
- 优先复用自研 `ProAGI`（Computer Use / 环境交互）的发布自动化思路
- 已有基础：`handlers/publish.py`（定时发布、平台变体、授权请求、审计）

**验收**
- [ ] 至少 1 个平台打通发布闭环（**fill + 存草稿**，人不点发布）
- [ ] `platform_variants` / `publish_jobs` 表已有，接通

**已推进（2026-09-12 · 第七批）：接口层与三态可用性 —— 协议里没有 `publish`**

S7 的第一步不是接 OpenCLI，而是**先把契约定下来**（照 S2 定 `ImageProvider` 的
先例）。落地为 `worker/runtime/providers/publish/`：

| 层 | 内容 |
|---|---|
| 协议 | `base.py`：`PublishProvider`（PEP 544 结构化协议）只有 `name` + `probe` |
| 状态 | `AvailabilityState` 三态：`ready` / `unavailable` / `need_login` |
| 映射 | `classify_exit()`：`sysexits.h` → 三态（借鉴 OpenCLI 语义，ADR-012） |
| 实现 | `opencli.py`：`shutil.which` + `<bin> doctor`；进程纪律照 `McpStdioClient` |
| 出口 | 命令 `ProbePublishProvider`（总线 + schema + 前端类型 + 结果契约 + CLI `publish provider`） |

⛔ **这一层最重要的设计是「没有什么」**：协议里**根本不存在 `publish`**。
把 ADR-008 写成「有 `publish` 方法但调用处拦住」，是让承诺停在注释里 ——
拦住一处调用，拦不住下一处；写成结构性缺失，才无法被绕过。
`worker/tests/test_publish_provider.py` 里那条断言是**变更检测器**而非现状描述：
日后有人给协议加动作，它当场红，逼那次改动去走 ADR。
同 `scripts/check_mcp_surface.py` 的 E 检查项一个原则 ——
**安全保证要么是可执行的，要么就是没有。**

两条刻意的取舍：

- **`fill` 这一轮不定**：契约照**已核实的事实**写，不照想象写。`probe` 的形状来自
  ADR-012 已核实的 `opencli doctor` + 退出码语义；而 `fill` 的参数形状取决于各家
  `draft` 命令的**实际**选项，没在真机上核实过就写死，后来者会照着一个错的契约
  去实现 —— 签名定错比没有签名更坏。
- **「未配置」≠「不认识」**：真机一跑才发现，拼错的渠道名（`opencil`）被报成
  「（环境变量）为空」—— 用户明明设了值，却被指去查一个不存在的「没配置」问题。
  现在点名那个值。**报错写错方向比不报还费时间。**

真机三态（本机未装 opencli）：未配置 → `unavailable` + 点名环境变量；
`opencli` 未装 → `unavailable` + 给出 `npm i -g @jackwener/opencli`；
`opencil` → 点名拼错值。`fill` / `NEED_LOGIN` 两条路径靠 `deps.publish` 注入
替身覆盖 —— 否则它们只有在装了外部工具的机器上才走过，等于长期无人看守。

**已推进（2026-09-13 · 第八批）：前端入口 —— 补上「只有 CLI 能到」的不对称**

上一批留了一条诚实记录：`check_ui_parity` 只查**一个方向**（GUI 可达 → CLI 有没有
入口），反向不查；`ProbePublishProvider` 当时只有 CLI 入口，门禁不会拦。这批补了前端：

| 交付 | 位置 |
|---|---|
| 判据层（纯函数 + 15 条单测） | `apps/desktop/src/features/publish/providerViewModel.ts` |
| 入口 | `PublishView.tsx` 顶部「发布通道」面板：进页即探测 + 可手动重新检测 |

三条**产品红线**钉在判据层（散在 JSX 里就只能靠肉眼）：

1. **只有 `ready` 算可用** —— 白名单，不是黑名单。写成「非 `unavailable` 就算可用」
   是这里最容易犯的错：后端将来多一个中间态就会被放行，而中间态恰恰是最不该被当成
   能用的那一类。未识别的状态值一律落「不可用 + **照实显示原始值**」。
2. **手动发布提示无条件成立** —— `MANUAL_PUBLISH_NOTICE` 刻意做成**常量而非函数**：
   没有输入可以依赖，也就没有「某种情况下忘了显示」。它还刻意**不读**后端的
   `auto_publish`（那是随填充包下发给插件/扩展的字段）—— 依赖它就会变成「后端漂移
   则 UI 沉默」，而那正是最不能沉默的地方。
3. **两种不可用的修法必须分开** —— `need_login`（去登录）与 `unavailable`（去装/去起）
   混成一句「不可用」会把用户送去查错误的方向。

负向验证（`.workbuddy/gate-negcheck/negcheck_ui_guards.py`，不进 git）：把 `isUsable`
改成黑名单写法、把两种状态的兜底文案合并，两条护栏都确认**会红且点名**目标测试，
还原后复跑恢复绿色。

**顺带发现一个死挂点（本轮未处理，另开一轮）**：`lib/useCommand.ts` 的 `useCommand` /
`useCommandMutation` **全仓零调用** —— 所有视图仍是 `runCommand` + 手写 `setLoading` /
`setError`。该文件注释写的是「改造前：79 处手写…」，读起来像改造已完成，实际调用点
一个都没改。本轮前端照**现有风格**写，没有引入第三种写法。

**依赖**：S2。**优先级低于 S3–S6**（发布不是北极星瓶颈）。
**风险**：OpenCLI 的适配器会随站点改版失效（上游自己都要 `autofix` 修）→
「发布失败」必须是**明确降级 + 人工兜底**，绝不静默失败。

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
