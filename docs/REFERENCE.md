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

| 仓库 | License | 可复用点 | 复用方式 | 对应模块 |
|---|---|---|---|---|
| `OrchClaw-Lite` | **MIT** | 项目级多 Agent 协作零依赖 demo | 直接 | Agent 编排模型（S6） |
| `huashu-design` | **MIT** | HTML 原生设计 skill（Agent-agnostic），高保真原型/动画/MP4 导出 | 直接 | 视觉模板与渲染（S2/S3） |
| `model-router` | **MIT** | 按任务类型自动路由最优模型 | 直接 | LLM 成本控制（S2） |
| `TencentDB-Agent-Memory` | NOASSERTION | Agent 长期记忆 4 层渐进管线，零外部依赖 | 借鉴/直接 | Memory 层（S6 后） |
| `douyin-live-info` | NOASSERTION | 抖音直播间信息获取（弹幕监听/数据统计/录制） | 借鉴 | **热点发现与平台数据采集（S5）** |
| `orchdesk` | 自研 | 多 Agent 编排桌面工作台（Electron + Cordis/dsh），9 插件装配 | 借鉴 | Agent 编排 UX、插件装配（S6） |
| `ProAGI` | 自研 | Computer Use / 环境交互 + 自学习 | 借鉴 | 平台自动化发布（S7） |
| `harness-agent` / `harness-agent-hermes` / `harness-agent-openclaw` / `agent-harnass` | 自研 | Agent 生命周期 6 阶段管理 + 强制阶段隔离 | 借鉴 | 流水线阶段治理（S2） |
| `zhiyi-new-agent-onboarding` | 自研 | New Agent 一键入职技能 | 借鉴 | Agent 注册/发现（S6） |
| `DustOrbit` / `orchclaw-agent-xinglu` | 自研 | 知识管理技能包 / Hermes leader 节点 | 借鉴 | 知识与编排（远期） |

---

## 3. 自有创作流水线（本 workspace 已验证资产）

目录：`C:/Users/my/WorkBuddy/2026-09-07-05-23-14/`

| 资产 | 来源项目 | 复用方式 | 对应模块 |
|---|---|---|---|
| Playwright 逐帧渲染 + ffmpeg 管道 | `gender-video/scripts/render.py` | 直接 | S1 `providers/renderer/playwright.py` |
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
| StepFun 生图 `POST /v1/images/generations`（step-2x-large / step-image-edit-2） | 厂商文档 | 直接（**临时**） | S2 image provider | 🚨 **2026-10-10 下线，官方无替代模型**（2026-09-08 核实） |
| StepFun 官方 ASR `/v1/audio/asr/file/submit+query` | 厂商文档 | 参考 | ASR 校验 | 用于验证 TTS 输出正确性 |
| Playwright（Python） | 开源（Apache-2.0） | 直接 | S1 渲染 | 本机 Remotion 装不上，改用 Playwright 逐帧 + ffmpeg 管道 |
| ffmpeg / ffprobe | 开源（GPL/LGPL，按构建） | 直接（外部二进制） | 渲染/合成 | 参数必须用 argv list，不拼 shell |

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
| `douyin-live-info` 的 License | NOASSERTION | 需确认其自有属性后再决定直接复用还是借鉴 |
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
