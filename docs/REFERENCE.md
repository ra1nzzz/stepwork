# REFERENCE — 引用来源登记

> **Status:** Active · **Date:** 2026-09-08
> **本文件职责**：记录功能与模块的引用来源——来自哪一份调研、哪一篇论文、哪一个仓库、哪一篇文档。
> **对应原则**：[`REPOSITIONING.md`](./REPOSITIONING.md) §3 P6（优先复用，禁止重复造轮子）。
> **复用方式**：`直接` = 代码/资产拿来用；`借鉴` = 授权不允许或形态不合，只取原理与实现方式重新实现。

---

## 1. 生态契约（最高优先级，不得自行重新定义）

| 来源 | 类型 | 授权 | 复用方式 | 对应模块 |
|---|---|---|---|---|
| `github.com/ra1nzzz/YT-Agent-Ontology`（private，PHASE 0 complete） | 母规范仓库 | 自有 | **直接（契约遵循）** | 全局语义层 |

**关键引用点**

| 契约 | 出处 | 说明 |
|---|---|---|
| `ContentOps` 域定义 | `ontology/DOMAIN-ONTOLOGY.md` §ContentOps `[Owner: STEPWORK]` | 素材/脚本/草稿/发布/渠道；挂靠 Material→Artifact、Script→Artifact(document)、发布流程→WorkflowDefinition |
| 核心语义骨架 | `ontology/YT-AGENT-ONTOLOGY.md` §3 | Human→Intent→Task→Agent→Runtime→Environment→Action→Event→Artifact |
| Identity 规范 | `ontology/YT-AGENT-ONTOLOGY.md` §2 | `{type}:{namespace}:{name}[-{seq}]`；换模型不换 Agent Identity |
| Event 信封 | `ontology/YT-AGENT-ONTOLOGY.md` §4.1 | `{id, type, actor, subject, timestamp, context, payload}`，`type = domain.action` |
| Artifact 骨架 | `ontology/YT-AGENT-ONTOLOGY.md` §10 | `{kind, uri, hash, verification, provenance, attempt}` |
| Task 生命周期 | `ontology/YT-AGENT-ONTOLOGY.md` §1.03 | created→decomposed→assigned→in_progress→submitted→reviewed→completed\|reworked\|failed |
| Workflow 双实体 | DECISION-LOG D-001 | Definition（版本化）与 Execution 强制分离 |
| Session/Task 解耦 | DECISION-LOG D-002 | Task 是工作对象，Session 是事件日志，禁止混用 |
| 迁移原则 | `README.md` 核心原则 | 真实产品价值 > 语义统一 > 契约统一 > 接口统一 > 实现统一；迁移走 兼容→映射→迁移→删除 |
| 迁移排期 | `phase0/15-RECOMMENDED-MIGRATION-ORDER.md` | PHASE 1-12；当前排期未含 STEPWORK（列为「相邻产品」） |
| 提示词范式 | `prompts/ORCHDESK-UNIFICATION-PROMPT.md` | 统一提示词结构（你是谁/遵循什么/不得重定义/必须兼容/只允许改/禁止改/验收） |

**⚠️ 缺口**：Ontology 仓库**尚无** `mappings/stepwork.yaml`、`products/stepwork/STEPWORK-ONTOLOGY-MIGRATION.md`、`prompts/STEPWORK-UNIFICATION-PROMPT.md`（已有 orchdesk/ordexa/inpeaknext/proagi/orchclaw 五份）。需在 S0 遗留项中补齐。

---

## 2. 自研仓库复用（ra1nzzz）

> 未声明 License 的自有仓库视为自研，可直接复用。

| 仓库 | License | 归属 | 可复用点 | 复用方式 | 对应模块 |
|---|---|---|---|---|---|
| `OrchClaw-Lite` | **MIT** | ✅ 自研 | 多 Agent 协议 + 任务状态机 + 三维评审 + 结项报告 | **直接复用** | Agent 原生（S6）+ Job 状态机（S1） |
| `model-router` | **MIT** | ✅ 自研 | 任务类型→模型路由 + 失败计数降级 + 成本优先 | **直接复用** | LLM 路由与降级（S2） |
| `huashu-design` | MIT（fork of `alchaincyf/huashu-design`） | ❌ 非自研 | HTML 原生设计 skill：三方向硬门、事实验证原则、分镜卡、镜头语言、MP4 导出、AI 看片评审 | **仅借鉴方法论** | 视觉模板与渲染（S2/S3）、QA（S3） |
| `douyin-live-info` | 🚨 **CC BY-NC 4.0**（fork of `qq564118922/douyin-live-info`） | ❌ 非自研 | 实时弹幕监听 / protobuf 解析 / 数据统计 / 录制 | ⛔ **仅借鉴原理，禁止引入任何代码** | 热点信号采集（S5） |
| `TencentDB-Agent-Memory` | NOASSERTION | 自研 | Agent 长期记忆 4 层渐进管线，零外部依赖 | 借鉴/直接 | Memory 层（S6 后） |
| `orchdesk` | 自研 | 多 Agent 编排桌面工作台（Electron + Cordis/dsh），9 插件装配 | 借鉴 | Agent 编排 UX、插件装配（S6） |
| `ProAGI` | 自研 | Computer Use / 环境交互 + 自学习 | 借鉴 | 平台自动化发布（S7） |
| `harness-agent` / `harness-agent-hermes` / `harness-agent-openclaw` / `agent-harnass` | 自研 | Agent 生命周期 6 阶段管理 + 强制阶段隔离 | 借鉴 | 流水线阶段治理（S2） |
| `zhiyi-new-agent-onboarding` | 自研 | New Agent 一键入职技能 | 借鉴 | Agent 注册/发现（S6） |
| `DustOrbit` / `orchclaw-agent-xinglu` | 自研 | 知识管理技能包 / Hermes leader 节点 | 借鉴 | 知识与编排（远期） |

---

## 2.1 四个仓库逐项借鉴清单（落到文件/接口级）

### ① `OrchClaw-Lite`（自研 · MIT · **直接复用**）

| 借鉴项 | 源码位置 | 落到 STEPWORK 哪里 |
|---|---|---|
| **Agent 消息协议**：`register / register-response / heartbeat / status-update / agent:message / task-execute / response` 11 种消息类型 + `PROTOCOL_VERSION` + `validatePublicMessage()` 逐字段校验 | `src/protocol.js` | S6 Agent 原生层：`worker/runtime/agent/protocol.py`，作为 STEPWORK 对外 Agent 互操作的内层消息格式 |
| **Agent 状态取值** `idle/working/busy/offline` + 能力声明 `capabilities[]` | `src/protocol.js` | Agent 注册表 `agent_registry` 表字段 |
| **任务状态机**：`pending → in_progress → submitted → reviewing → completed / returned`，显式 `TASK_TRANSITIONS` 转移表 + `canTransitionTask()` 守卫 + `promoteNextPendingTask()` | `src/task-state-machine.js` | 升级现有 `JobStage`：补 `SUBMITTED`（待审）与 `RETURNED`（打回重做），转移表化，禁止任意跳变 |
| **三维评审**（质量 / 效率 / 可复用）+ 通过或打回 → 打回后回到 `in_progress` | README Demo Flow | S3 QA：`qa.py` 输出结构化评审，失败可触发 `RETURNED` 而非直接失败 |
| **结项报告**（含 Review 汇总与可沉淀经验） | README | 每个 Job 完工后生成 `closeout.md`，S8 反哺选题 |
| 零依赖 + `npm test` 即跑 + Release Gate CI | `.github/workflows/release-gate.yml` | 借鉴：S1 的验收门禁形态 |

---

### ② `huashu-design`（非自研 · MIT · **仅借鉴方法论**）

> 授权上 MIT 允许直接复用，但按产品策略只借鉴方法论，不搬运代码。

| 借鉴项 | 出处 | 落到 STEPWORK 哪里 |
|---|---|---|
| **三方向硬门**：任何新视觉产出 100% 先出三个差异化方向初稿让用户选，指定风格也不豁免 | `SKILL.md` 任务路由表 | S3 视觉风格层：新增风格/模板时先出 3 版 `preview` 供选，选定后才全量渲染 |
| **核心原则 #0 · 事实验证先于假设**：涉及具体产品/事件/版本号的断言必须先检索验证，禁止凭语料下断言 | `SKILL.md` | **S5 热点追踪强相关**：选题里的事实性断言（"某某已发布""某数据"）必须走检索核验，否则产出错选题 |
| **任务路由表（一张表定入口，多信号叠加）** | `SKILL.md` | 借鉴为 STEPWORK 的「能力需求 → 模板/Provider」路由表（我方案 §3.7 的风格注册表） |
| **轻量分镜卡**（每镜 8 字段，"每一镜先是一张会动的封面"）+ 镜头语言（zoom/pan/转场） | `references/storyboard-basics.md`、`camera-language.md` | S2 分幕契约增强：`video_scenes` 表补「镜头类型/构图」字段，避免幕=切页的幻灯片感 |
| **解说驱动动画铁律**：先有解说词，再按**音频实测时长**驱动画面；失败模式 #1 是"做成带配音的 PPT" | `references/voiceover-pipeline.md` | 印证我们现有 `timeline.json`（TTS 实测时长反推幕时长）的做法是对的，写进 S2 验收标准 |
| **HTML 动画导出 MP4 规格表**（25/60fps、CRF 18、GIF palette 优化）+ `render-video-seek.js` **按时间轴 seek 直录** | `references/video-export.md`、`scripts/render-video.js` | S1 渲染器：与我们的 `__setTime` 跳变逐帧方案同源，可对照补 60fps 与 GIF 输出规格 |
| **AI 看片评审闭环**：终渲后喂视觉模型按 checklist 出结构化报告（60s 分段、带导演稿上下文区分"设计意图"与"bug"） | `references/ai-video-review.md`、`scripts/cloud/ai-review-video.py` | S3 QA 二期：自动看片 + 人工抽帧双轨；**它带 `--context 导演稿` 这个设计值得直接抄思路** |

---

### ③ `model-router`（自研 · MIT · **直接复用**）

| 借鉴项 | 源码位置 | 落到 STEPWORK 哪里 |
|---|---|---|
| **任务类型 → 模型路由表**（`code / reasoning / fast / creative / auto`，各带 `primary` + `fallbacks[]`） | `models.json` | S2 LLM 层：STEPWORK 的 LLM 调用有 4 类（选题挖掘 / 文案创作 / 深奥内容翻译大白话 / 事实核验），各自路由不同档模型 |
| **成本优先策略**：`priority: ['free','flash','fast']`，优先免费/低耗模型 | `handler.js` DEFAULT_MODELS | 直接吸收：文案草稿用免费档，终稿用高质量档 |
| **失败自动降级**：`modelErrorTracker` 滑动窗口（5 分钟 / 3 次错误）后切 fallback + 超时重试 | `handler.js` | S2 LLM provider：STEPWORK 目前无降级，直接补上；与图像 Provider 停服风险共用同一套降级机制 |
| **手动覆盖语法** `[model: bailian/qwen3-coder-plus]` | README | 借鉴为 CLI/GUI 的 `--model` 显式指定 |
| Hook 自动注册、模型切换提示 | `hook.js` / `global-hook.js` | 借鉴交互形态：切换模型时在进度日志里可见 |

---

### ④ `douyin-live-info`（非自研 · 🚨 **CC BY-NC 4.0** · **仅借鉴原理，禁止引入代码**）

> ⛔ **红线**：父仓库许可为 **CC BY-NC 4.0（禁止商业使用）**。STEPWORK 有商业化意图，**不得复制、改写、分发其任何源码或资源**（含 protobuf 定义、签名算法实现）。只允许在产品文档中记录下列**架构思路**，由我们从零实现。

| 借鉴项（仅思路） | 参考位置 | 落到 STEPWORK 哪里 |
|---|---|---|
| **实时弹幕 = 热点信号源**：把直播间弹幕当作选题的即时需求信号（用户实时在问什么） | `electron/services/danmu.ts` | S5 热点 MCP Server 的信号源之一：弹幕高频词 → 选题候选 |
| **长连接消息流架构**：WebSocket 订阅 + 心跳保活（`HEARTBEAT_BYTES` 固定字节、定时重连）+ gzip 解压 + 结构化解析 | `danmu.ts`、`protobuf-parser.ts` | 借鉴为采集器通用骨架：`subscribe → heartbeat → decompress → parse → normalize`，对任何实时源通用 |
| **消息归一化模型**（`type/userName/content/giftName/.../rawData` 保留原始报文） | `danmu.ts` `DanmuMsg` 接口 | 借鉴：热点条目统一保留 `raw` 原始报文，便于回溯与重解析 |
| **本地落盘 + 导出**：SQLite 存储 + CSV/JSON 导出 + 多房间批量管理 | `services/database.ts` | 借鉴：`hotspot_items` 表设计 + 批量源管理 |
| **Electron 桌面端 + ffmpeg 录制** | `electron/`、`recording-manager.ts` | 与我们 Tauri 路线同类，可参考其 ffmpeg 二进制放置策略 |

---

## 3. 自有创作流水线（本 workspace 已验证资产）

目录：`C:/Users/my/WorkBuddy/2026-09-07-05-23-14/`

| 资产 | 来源项目 | 复用方式 | 对应模块 |
|---|---|---|---|
| Playwright 逐帧渲染 + ffmpeg 管道 | `gender-video/scripts/render.py` | 直接 | ✅ S1 `providers/renderer/playwright.py`（已落地） |
| stepfun 复刻音色 + atempo 归一化 + MD5 缓存判重 | `*/scripts/gen_tts.py` | 直接 | S2 TTS provider |
| 音频拼接 + timeline 生成 | `*/scripts/build_audio.py` | 直接 | S2 compose |
| 分幕数据契约（`scenes.json`） | `*/scenes.json` | 直接 | S2 `video_scenes` 表设计 |
| 视觉模板（楷体/Ken Burns/不闪烁/分屏特写） | `*/design_*.html` | 直接 | S3 模板层 |
| 抽帧目检脚本 | `*/scripts/still_*.py` | 直接 | S2 QA |

**来源方法论**

| 方法论 | 出处 | 说明 |
|---|---|---|
| 创作者六维 DNA（Content/Hook/Narrative/Explosion/Language/Conversion） | `douyin-ego-creator` SKILL（观雅集下载，基于 168 篇逐字稿蒸馏） | S4 `style_dna` 字段设计依据 |
| 小黑怪诞配图风格 | `ian-xiaohei-illustrations` SKILL（GitHub） | 美术风格 prompt 模板 |

---

## 4. 外部服务与厂商文档

| 来源 | 类型 | 复用方式 | 对应模块 | 备注 |
|---|---|---|---|---|
| StepFun TTS `POST /step_plan/v1/audio/speech`（stepaudio-2.5-tts）+ 音色复刻 `/voices` | 厂商文档 + 实测 | 直接 | S2 TTS | **实测坑**：`instruction` 情绪指令会盖过 `speed` 参数 → 必须生成后用 `atempo` 归一化 |
| ~~StepFun 生图 `POST /v1/images/generations`~~ | 厂商文档 | ❌ **已废弃** | — | 🚨 **2026-10-10 公告下线；2026-09-09 实测 `GET /v1/models` 已无任何文生图模型**（只剩图生图 `step-image-edit-2`），实际提前失效 |
| 智谱 CogView-4 `POST https://open.bigmodel.cn/api/paas/v4/images/generations` | 厂商文档 + 第三方复核 | 直接 | S2 `providers/image/openai_compatible.py` | 同步返回 `data[0].url`（30 天有效）；尺寸须 512–2048、16 整除、≤2^21 px → 9:16 取 `1088x1920`；**不支持 `response_format`** |
| 硅基流动 `POST https://api.siliconflow.cn/v1/images/generations` | 厂商文档 | 直接（同上适配器） | S2 `providers/image/openai_compatible.py` | 尺寸字段文档写作 **`image_size`**（非 OpenAI 标准 `size`），预置已按此设 `size_key`；Kolors 为长期免费档 |
| 阿里云通义万相 / `qwen-image` | 官方文档 | ❌ **不接** | — | 官方明确「图像生成模型走 DashScope 原生 API，**不支持 OpenAI 兼容（compatible-mode）**」：端点 `/api/v1/services/aigc/...`、尺寸 `W*H` 星号分隔、响应 `output.choices[0].message.content[0].image`。真要选它需单开适配器 |
| StepFun 官方 ASR `/v1/audio/asr/file/submit+query` | 厂商文档 | 参考 | ASR 校验 | 用于验证 TTS 输出正确性 |
| Playwright（Python · Apache-2.0，自带 Chromium） | 开源 | 直接 | **S1 `providers/renderer/playwright.py`** | 已实装「Chromium 逐帧截图 + ffmpeg `image2pipe` 管道直连」。本机装不上 Remotion 才用 Playwright；进度由帧数驱动（管道 ffmpeg 读不出总时长） |
| `fakes/fake_ffmpeg_pipe.py`（仓库内） | 自研 | 直接 | 测试 | fake ffmpeg 读 stdin 数 JPEG SOI 写 JSON 报告；`STEPWORK_FAKE_FFMPEG_SLEEP=1` 睡 30s 给取消测试证明 terminate 真生效 |
| ffmpeg / ffprobe | 开源（GPL/LGPL，按构建） | 直接（外部二进制） | 渲染/合成 / 时长探测 | 参数必须用 argv list，不拼 shell；WinGet 装的常不在 PATH，本仓库用 `STEPWORK_FFMPEG_BIN` 显式指定 |

> ⚠️ **ffmpeg 授权**：STEPWORK 为 AGPL-3.0，与 GPL 兼容；若未来改双许可闭源，需确认 ffmpeg 构建版本（LGPL vs GPL）的链接方式。己见 `LICENSE_AUDIT.md`（已归档，待更新）。

---

## 5. 方法借鉴（授权不允许直接取用 / 仅取原理）

| 灵感来源 | 借鉴了什么 | 对应模块 |
|---|---|---|
| Remotion 的分镜与音画对齐思路 | 分幕时间轴驱动渲染的编排模型（**未取代码**，本机装不上 Remotion） | S2 timeline |
| 各短视频平台的发布自动化实践 | 环境交互 + 表单填充的抽象（未取代码） | S7 发布 |

---

## 6. 待补充 / 存疑

| 项 | 状态 | 说明 |
|---|---|---|
| 生图 Provider 长期选型 | **未定** | StepFun 10-10 停服后：通义万相 / CogView-4 / 硅基流动 / 本地 SDXL，需实测中文字形与风格一致性 |
| 热点数据源合规性 | **未调研** | RSS/GitHub Trending 一般合规；平台榜单与评论抓取需注意 ToS。STEPWORK `ingest/download.py` 已明令禁止反爬/风控规避 |
| ~~`douyin-live-info` 的 License~~ | ~~NOASSERTION~~ → ✅ **已确认 CC BY-NC 4.0** | 2026-09-08 核实：fork 自 `qq564118922/douyin-live-info`，父仓库 **CC BY-NC 4.0（禁止商用）**。结论：**仅借鉴架构思路，禁止引入任何代码**。已从「待确认」转为红线，见 §2.1 ④ |
| 数字人服务提供商 | **未调研** | 按分钟计费与排队，需成本模型 |
| 视频素材版权来源 | **未调研** | C 版（素材版）前置依赖 |

---

## 7. 登记规则

新增引用时必须记录：

1. **来源**（仓库 URL / 文档链接 / 论文 DOI / 调研文件）
2. **授权**（SPDX 或「自研」/「未声明」）
3. **复用方式**（直接 / 借鉴）
4. **对应模块**（落到哪个文件或阶段）
5. **备注**（风险、停服时间、已知坑）

> 授权不允许直接取用的，**必须**在「复用方式」标 `借鉴`，并说明借鉴了什么——这是对原作者的尊重，也是避免未来合规风险。
