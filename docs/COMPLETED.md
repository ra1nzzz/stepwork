# COMPLETED — 已完成的功能与模块

> **Status:** Active · **Date:** 2026-09-08
> **本文件职责**：记录已完成的功能与模块。**在办与规划**请见 [`ROADMAP.md`](./ROADMAP.md)。
> **判断标准**：只记录**真实可用**的。跑得通但产出为空的，记入 §3 假实现清单，不计入完成。

---

## 1. 仓库内已实现（STEPWORK）

> 代码基线：`main @ bb31569`，136 commits，作者 ra1nzzz。
> 真实代码全部位于 `worker/runtime/`（120 py / 16421 行）。

### 1.1 工程地基 ✅

| 模块 | 落点 | 说明 |
|---|---|---|
| 命令总线 | `worker/runtime/commands/bus.py` | `_ROUTES` ~90 条，`commandType → handler 模块`，importlib 懒加载；含 Agent 白名单与幂等 |
| 任务状态机 | `jobs/engine.py` + `lease.py` + `lifecycle.py` + `cancel.py` | 8 态 × 11 阶段，带 lease、heartbeat、`max_attempts`、取消 |
| 依赖注入 | `runtime/deps.py::Deps` | repos/ingest/asr/ai/tts/renderer/scene_detector/notify |
| 存储 | `db/connection.py`（SQLite + WAL）+ `migrations/0001..0011` | 22 张表；down 脚本经真往返测试 |
| 契约防漂移 | `results/registry.py` + `scripts/gen_result_types.py` | pydantic → TS 类型生成 → CI `--check` 强制同步 |
| 审计 | `migrations/0002` `audit_events` + `runtime/audit.py` | 命令级审计，含 `event_type` / `payload` |
| 可观测性 | `runtime/observability.py` + `handlers/diagnostics.py` | 诊断包导出 |

### 1.2 通信与外壳 ✅

| 模块 | 落点 | 说明 |
|---|---|---|
| Tauri 桌面端 | `apps/desktop/`（107 前端文件 + 30 Rust 文件） | React 18 + Vite 5 + Tauri 2 + zustand；无 router，用 `useViewStore` 切视图 |
| 设计系统 | `styles/tokens.css` + `components/primitives.tsx` | oklch 色板、间距阶、z-index、动效时长，含 `prefers-reduced-motion` |
| Sidecar RPC | `src-tauri/src/sidecar/{spawn,rpc_client,heartbeat}.rs` + `runtime/rpc.py` | JSON-RPC 2.0 over stdio，4 字节长度前缀帧，`MAX_FRAME_SIZE=1MiB` |
| 开发态桥 | `worker/dev_bridge.py` | HTTP 127.0.0.1:8787 + Bearer，便于前端独立调试 |
| CLI | `cli/`（`stepwork-cli`）+ `cli/tests/test_cli.py`（58 例） | 与命令总线共享契约 |
| MCP Server | `mcp/server.py`（9 只读工具） | 显式永不暴露 `UpdateConfig` |
| 前端测试 | vitest ~70 例 / 10 文件 | 含设计系统与数据层覆盖 |

### 1.3 领域能力 ✅

| 模块 | 落点 | 完成度 |
|---|---|---|
| LLM | `providers/ai/{base,cloud,openai_compatible}.py` | httpx 真实 POST；`complete(prompt, schema)` 结构化输出 |
| ASR | `providers/asr/whisper.py` | 真 faster-whisper（可选依赖 `.[asr]`，默认不装） |
| TTS | `providers/tts/edge.py` | 真 edge-tts（可选 `.[tts]`） |
| 渲染（基础） | `providers/renderer/ffmpeg.py` | 真跑 ffmpeg，但**仅「纯色背景 + 一行 drawtext」** |
| 渲染模板 | `render/templates.py` | 4 个模板 + 3 画幅预设；未注册模板抛 `KeyError`（不静默回退） |
| 字幕 | `render/subtitles.py::build_srt` | 真 SRT |
| 时间线导出 | `render/edit_export.py` | 真 OTIO + CMX3600 EDL |
| 素材导入 | `handlers/import_source.py` + `ingest/` | 直链下载 + hash + ffprobe 元数据 |
| JobStage | `migrations/0002..0004` + `jobs/stage.py` | `proposing / scripting / synthesizing / rendering / publishing / verifying / drafting / approving` 9 段，与 JobState 解耦 |
| 选题角度 | `topic/{prompt,parse}.py` + `handlers/generate_topic.py` | `TOPIC_SCHEMA` 强约束（标题/差异/hook/受众/观点/风险） |
| 脚本生成 | `script/{prompt,parse,diff,history,paragraph,similarity}.py` + `handlers/generate_script.py` | 含版本 diff、历史、段落编辑、相似度 |
| 品牌档 | `handlers/brand.py` + `migrations/0005` | `brand_profiles` + `brand_reference_scripts` + `format_brand_prompt_block()` + `RecordPreference` |
| 版本管理 | `ContentVersion`（`parent_version_id` 版本树 + `content_hash` + `producer`） | 溯源就绪 |
| 发布（审计层） | `handlers/publish.py` + `migrations/0003` | 定时发布、平台变体、授权请求、审计；**真实发布执行未实现** |
| 审批 | `handlers/approvals.py` | 创建/列表/决策 |
| 溯源 | `handlers/provenance.py` | AI 标签与来源追溯 |
| 项目导入导出 | `handlers/project_io.py` + `backup.py` | 含工作区备份/恢复 |
| 工作区 | `handlers/workspaces.py` | 创建/重命名/归档 |
| 维护 | `handlers/maintenance.py` + `runtime/cleanup.py` | 清理、审计列表 |

### 1.4 Agent 互操作 ✅（子集实现，非占位）

| 模块 | 落点 | 说明 |
|---|---|---|
| 出站 MCP 客户端 | `agents/mcp_client.py::McpStdioClient` | `initialize` / `list_tools` / `call_tool` |
| A2A | `agents/a2a_{card,client,server}.py` | Agent Card、`/.well-known/agent.json`、6 个 SKILLS 映射；协议 v0.2.0 |
| ACP | `agents/acp_client.py`（391 行） | `start` / `new_session` / `prompt` / 权限结果构造 |
| 公共层 | `agents/channel.py` | 连接加载、能力同步、调用记录 |

> 注：以上均为**真实协议实现**，但联调用的是 `worker/tests/fakes/` 假 Agent 脚本，未接真实三方服务。

### 1.5 S1 · Playwright 逐帧渲染器（S1 探路 · 2026-09-08）

> **从 [`ROADMAP.md` §S1](./ROADMAP.md) 移入**：探路目标「逐帧渲染能否在 Job/进度/取消框架里工作」已验证——这是最大未知数，通过之后 S2–S8 均为照模式复制。

**新增代码**

| 模块 | 落点 | 说明 |
|---|---|---|
| 渲染器实现 | `worker/runtime/providers/renderer/playwright.py` | 实现 `RendererProvider` 协议（`render:frame-by-frame-v1`）。Chromium 逐帧截图 → ffmpeg `image2pipe` 管道直连，**不落盘中间帧**；真进度 = 已写帧数 / 总帧数；取消无僵尸（复用 `FFmpegRunner.supervise`） |
| 默认文档 | `worker/runtime/render/assets/s1_probe.html` | 零外部依赖（无字体/无图片/无网络）HTML，仅用于让 provider 开箱即可跑通一次真渲染。**不是正式模板**（S3 才动 `templates.py`） |
| 抽帧目检 | `scripts/still_frames.py` | 从 `*/scripts/still_*.py` 搬运并泛化，命令行 `--html / --durations / --out-dir / 时刻…`。照搬两坑：先回退 0.8s 让淡入完成；撞切句瞬间前移 0.6s |
| 时长探测 | `worker/runtime/render/ffmpeg_runner.py::probe_duration` + `FFmpegRunner.probe` | ffprobe 优先，退回 `ffmpeg -i` 的 stderr Duration 行。ffprobe 候选：显式 → `PATH` → ffmpeg 同目录（WinGet 布局） |
| 协议拆分 | `FFmpegRunner.{spawn, supervise}` + `run` | 把「启动」与「等待」拆开，使调用方能往 stdin 喂帧并**复用完全相同的取消/超时/回收语义**。`require_bin()`（原 `_require_bin`）变体：二进制路径不在文件即抛 |
| `RenderSpec` 扩字段 | `worker/runtime/models.py` | `style_id="illustration"` / `art_style="xiaohei"` / `image_set_id=None`（全部带默认值，**既有调用方与现有表结构不受影响**） |
| Provider 解析 | `worker/runtime/providers/resolve.py::resolve_renderer` | `STEPWORK_RENDER_PROVIDER=playwright`（默认仍 ffmpeg）；`STEPWORK_FFMPEG_BIN` 显式指定（WinGet 装的不在 PATH）；playwright 包缺失 → `None`（handler → UNAVAILABLE），**不静默回退 ffmpeg** |

**测试**

| 文件 | 数量 | 覆盖 |
|---|---|---|
| `worker/tests/test_playwright_renderer.py` | 10 + 1 perf | 正常（fake 数 JPEG SOI 确认帧进了 ffmpeg）/ 取消（elapsed < 15s + `last_proc.poll() is not None`）/ ffmpeg 不可用 / 协议一致性 / 音频不存在 / 文档缺 `__setTime` / resolve 三分支 / e2e（1080×1920 h264 30.0s 70.4s 墙钟） |
| `worker/tests/fakes/fake_ffmpeg_pipe.py` | — | fake ffmpeg：`stdin.buffer.read()` 数 SOI 写入 JSON 报告；`STEPWORK_FAKE_FFMPEG_SLEEP=1` 时睡 30s，**让 cancel 测试能证明子进程被 terminate** |

**验收结果**

| 项 | 结果 |
|---|---|
| 30 秒 9:16 成片 | ✅ `D:/Code/StepWork/.workbuddy/s1-acceptance/draft_s1-acceptance.mp4`（2,607,475 B，wall=71.5s，progress=901 点单调递增） |
| ffprobe 规格 | ✅ `h264 yuvj420p 1080×1920 r=30/1  dur=30.000000s · aac 44100Hz mono` |
| `progress_cb` 真驱动 | ✅ 已写帧数 / 总帧数（非 ffmpeg stderr Duration 解析——管道输入 ffmpeg 读不出总时长） |
| 中途取消 0 僵尸 | ✅ 第 10/25 帧取消；`FFmpegCancelled` 抛出 + `r.last_proc.poll() is not None`；实测 elapsed < 15s（fake `--sleep` 不让等 30s） |
| 原 `FFmpegRenderer` 不受影响 | ✅ `test_render.py` 5 例 + `test_ffmpeg_runner.py` 3 例全绿；协议 dual-instance 通过 |
| `mypy strict` | ✅ 203 source files / 0 errors |
| `ruff` | ✅ `worker/` + `scripts/` All checks passed |
| `pytest -m "not perf"` | ✅ 685 passed, 7 deselected in 197.76s |

**搬运的已验证资产**

| 来自 | 落点 |
|---|---|
| `C:/Users/my/WorkBuddy/2026-09-07-05-23-14/gender-video/scripts/render.py` | `worker/runtime/providers/renderer/playwright.py`（管道直连、image2pipe、进度墙钟、-shortest） |
| `C:/Users/my/WorkBuddy/2026-09-07-05-23-14/gender-video/scripts/still.py` & `still_illust.py` | `scripts/still_frames.py`（合并泛化） |

**新增遗留项**（写给 S2）——见 [§5 文档治理 / 新增未完成项](#5-文档治理)

**S1 → S2 衔接（2026-09-09 · 本轮清理结果）**

| 项 | 落点 | 说明 |
|---|---|---|
| per-request renderer hint | `resolve.py::renderer_from_hint` + `handlers/render_source.py` | `payload.renderer`（字符串或 `{"kind": ...}`）覆盖 `deps.renderer`；复用调用方 `FFmpegRunner`；缺失/未知回落默认。**补上 P4 缺口**——只靠 env 时同一进程无法按项目换渲染器 |
| `design_doc_uri` 正名 | `models.py::RenderSpec` + `providers/renderer/playwright.py` | 新增字段作正名，`background_uri` 降为遗留别名（优先级：design_doc_uri → background_uri → 构造参数 → 内置探路文档），避免单字段多义 |
| `render` 可选依赖 | `pyproject.toml` | `[project.optional-dependencies].render = ["playwright>=1.40"]`；装完仍需 `playwright install chromium`（pip 装不了浏览器二进制） |
| `JobStage.ILLUSTRATING` | `models.py::JobStage` | 配图阶段；只增不改名，既有 11 个取值不变（`jobs.stage` 列是 TEXT，无 CHECK 约束） |
| `video_scenes` 表 | `migrations/0012_video_scenes.sql` + `.down.sql` | S2 地基：`(id, version_id, seq, text, emotion, highlight, audio_uri, image_uri, start_sec, duration_sec, born_at_sec, created_at)`；`UNIQUE(version_id, seq)` 防并发写幕互相覆盖 |
| **`video_scenes` 读写层（2026-09-09 追加）** | `models.py::VideoScene` + `db/repos.py::VideoSceneRepo` + `handlers/video_scenes.py` + `cli/__main__.py` | **表建了必须有代码读写**，否则又是一张空表。三命令 `SaveVideoScenes` / `ListVideoScenes` / `UpdateVideoScene`；CLI `stepwork-cli scenes {save,list,update}`；`ListVideoScenes` 进 Agent 白名单（只读），写命令降级为待审批任务 |
| 迁移清单漂移修复 | `migrations/README.md` | 清单原停在 0005，补登记 0006–0012（README 自己规定「新增迁移必须追加一行」，此前 6 个版本漏登） |

---

## 2. 已验证的创作流水线（本 workspace 资产，可直接搬）

目录：`C:/Users/my/WorkBuddy/2026-09-07-05-23-14/`

| 项目 | 成片 | 规格 | 验证 |
|---|---|---|---|
| `gender-video/` | `性别对立的本质_配图版_v2_9x16.mp4` | 1080×1920 / 259.5s / H.264+AAC | 抽帧 + 字幕像素检测通过 |
| `ai-anxiety/` | `AI时代的焦虑_配图版_9x16.mp4` | 1080×1920 / 189.6s | 目检通过 |
| `learn-ai-anxiety/` | `用学习对抗AI焦虑_配图版_9x16.mp4` | 1080×1920 / 104s | 目检通过 |

**可搬运资产**

| 资产 | 说明 |
|---|---|
| `scripts/render.py` | Playwright 逐帧渲染 + ffmpeg 管道直连；已支持 `--html` / `--test N` |
| `scripts/gen_tts.py` | stepfun 复刻音色 + `atempo` 语速归一化 + **MD5 + 字节数双判据**缓存判重 |
| `scripts/build_audio.py` | 分段音频拼接 + `timeline.json` 生成 |
| `scripts/still_*.py` | 抽帧目检（含「跳变 `__setTime` 淡入失效」绕法） |
| `design_*.html` | 楷体字幕、Ken Burns 缓推、同图跨幕不闪烁、末幕分屏 80px 红色特写 |
| `scenes.json` / `timeline.json` | 分幕数据契约（id / text / emotion / highlight） |

**已固化的隐性规则**（必须写成断言，不能只靠 LLM 自觉）
- `CLOSING_PHRASE` 必须在末幕最后一句且 `idx > 0`，否则分屏特写静默失效
- 每幕 `highlight` 必须是该幕 `text` 的子串，否则标红静默失效

---

## 3. ⛔ 已知假实现（不计入完成）

| 位置 | 真相 | 风险 |
|---|---|---|
| `providers/asr/local.py::LocalASRProvider` | 硬编码 5 行中文假台词，按 URI 哈希轮转 | 未装 `.[asr]` 时链路「成功」但转写为假 |
| `providers/tts/local.py::LocalTTSProvider` | 静音 WAV，只按字数算真实时长 | 未装 `.[tts]` 时链路「成功」但成片无声 |

> **处理建议**：S2 阶段把这两个 provider 的 `UNAVAILABLE` 语义收紧——缺依赖时应明确报错，而不是产出空内容。

---

## 4. 空壳目录（不是完成项，也不是待办项）

以下目录仅含 `.gitkeep`，**不代表已实现**：

```text
core/{domain,commands,application,events,policies}
worker/{tasks,providers,media}
sdk/{python,typescript,plugin,agent-adapter}
publisher-engine/{browser,dom,rpc,runtime,uploader}
plugins/{official,registry}
```

对应能力请查 `worker/runtime/` 下的真实实现，或见 [`ROADMAP.md`](./ROADMAP.md)。

---

## 5. 文档治理

| 时间 | 事项 |
|---|---|
| 2026-09-08 | 旧规划文档 22 份归档至 `docs/archive/legacy/`，建立 `REPOSITIONING` / `ROADMAP` / `COMPLETED` / `REFERENCE` 新体系 |
| 2026-09-08 | 核实 YT-Agent-Ontology 契约：STEPWORK = `ContentOps` 域 Owner |
| 2026-09-08 | 核实 StepFun 生图 2026-10-10 下线（官方无替代模型） |
| 2026-09-08 | **S1 完成**：Playwright 逐帧渲染器探路通过；ROADMAP 打勾、COMPLETED §1.5 入账、REFERENCE 登记新依赖；详见上方 §1.5 |
| 2026-09-09 | **S0 遗留闭合**：向 YT-Agent-Ontology 补 `mappings/stepwork.yaml`（13 实体）+ `products/stepwork/STEPWORK-ONTOLOGY-MIGRATION.md`（13 节）+ `prompts/STEPWORK-UNIFICATION-PROMPT.md`，入 `YT-Agent-Ontology@b1089e9`；PHASE 编号裁决写入 **D-008**（不新增生态 PHASE；三件套计 PHASE 3 兼容层资产；ContentOps 基线须在 PHASE 9 前冻结）。顺带修母规范 `inpeaknext` / `orchclaw` / `proagi` 三份映射的 YAML 语法（此前声称机器可读但 `yaml.safe_load` 报错），六份映射现已全部可解析 |

| 2026-09-09 | **S5 最小验证**：建独立仓库 `ra1nzzz/stepwork-hotspot-mcp`（AGPL、零运行时依赖、stdio MCP），三源（HF Daily Papers / GitHub Trending / RSS）全部免密钥。STEPWORK 的 `McpStdioClient` 直连全通、真实出 12 条热点 0 errors |
| 2026-09-10 | **S5 结论翻案 + 中文热点全面打通**：第一版「中文热搜拿不到」是**探测方法问题**（用第三方镜像代表微博、没跟 http→https 重定向把 arXiv 判死、默认 UA 触发 InfoQ 的 WAF 451）。实测补上 **抖音热榜**（官方）、**今日头条热榜**（官方，50 条 + HotValue）、**NewsNow 五榜**（微博/知乎/头条/百度/B站）+ arXiv + InfoQ —— **11 源真实抓取 60 条 0 errors**。修掉两个「只有跑真数据才暴露」的缺陷：① 全局排序+截断让「用抓取时刻当时间」的源吃光限额（头条 50 条挤掉微博/知乎/B站）→ 改**按源分配额 + 交错**；② 聚合源内部同样问题 → 每个榜单注册成独立源。抖音另两条路径已调研：热点宝 `douhot.douyin.com`（10 大榜单/200+ 垂类/热词趋势/活动日历，价值最高但**无公开 API**，只能浏览器自动化 + 登录态）；抖音开放平台 API **个人不可申请**（要企业资质，热榜不在开放能力清单）。今日热榜 `tophub.today` 可抓（服务端渲染）但成本高于 NewsNow，未接。详见 `ROADMAP` S5 |
| 2026-09-10 | **热点宝（`douhot.douyin.com`）接入**：无公开 API（微前端 SPA，数据在登录后带 `a_bogus` 签名的 XHR 里）→ 走 **CDP 复用用户已登录浏览器**（`--remote-debugging-port`），**不伪造签名**、只读、不碰密码/cookie/profile。playwright 列 `[browser]` **可选**依赖（上游「零运行时依赖」立身之本不破）；未装/未连/未登录一律 `SourceError` 明示，不静默返空。`discover()` 新增 `skipped`（与 `errors` 分开：没打算抓 ≠ 抓失败），需登录源**默认不参与全源抓取**，否则「没开浏览器」会变成每次调用都带噪音错误。顺手修 README 里 InfoQ「451 不可用」的过时结论。上游 `stepwork-hotspot-mcp@3af4d0c` |
| 2026-09-10 | **修既有缺陷：取消渲染后留下半成品 mp4**（S1/S3 遗留，`test_render_cancel_no_zombie` 一直红）。`playwright.py` 的异常路径只收子进程、没删 `draft_<ver>.mp4` —— 输出文件名与**成品同名同形**，取消/失败后留下的是一个「名字正常、内容损坏」的 mp4，会被后续流程当成品用。新增 `_discard_partial()`，**所有异常路径**都清（失败可能发生在 ffmpeg 打开输出之前，故吞 `FileNotFoundError`） |
| 2026-09-10 | **S5 热点筛选与推荐机制落地**（弈韬：「产品本身是 Agent，需要有热点筛选和推荐机制」）：新增 `worker/runtime/hotspot/`（`mcp.py` 对接 / `rank.py` **纯函数**打分 / `models.py` 事实形状）+ `migrations/0014`（`hotspot_items` + `hotspot_feedback`）+ 4 个命令（`ListHotspotSources` / `DiscoverHotspots` / `RecommendHotspots` / `RecordHotspotFeedback`）+ CLI `hotspots` 子命令 + 结果契约。五维加权打分（品牌契合 0.30 / 时效 0.30 / **源内分位**热度 0.20 / 新颖度 0.10 / 反馈 0.10），禁用表达是**乘性**惩罚。理由由 AI 写、失败降级规则模板且 `reasonSource` 如实标 `rule`。反馈闭环可改变下一轮排序。发现与推荐**分成两个命令**：发现取事实（换源不影响下游），推荐下判断（吃画像/历史/反馈），合一个就没法「换个角度重推」而不重抓全网 |
| 2026-09-10 | **热点 → 选题打通（方案 A，弈韬裁决）**：新增 `ConvertHotspotToTopic` + `hotspot/brief.py`（**纯函数**拼装）+ `HotspotRepo.get` / `ContentVersionRepo.find_by_hash`。热点是**外部未核实**内容（PRD-AGT-003），不能直接产出选题 —— 中间落一份「选题简报」`content_versions(content_type='hotspot_brief')`，`producer` 带 `trustLevel=external-unverified` + `reviewState=pending_review`（**复用** `agents/channel.py` 的同一套常量，UI 一套逻辑筛出全部待复核产物）+ `sourceUrl`/`hotspotId`/`batchId`。免责头写在**正文里**（下游读的就是 content 文本，写别处躲不开）。理由与打分分解**由调用方回传、命令不重算不猜**，没带就如实写 `reason_source="none"`（**绝不编一句听起来合理的理由**）。同输入按内容哈希复用（Agent 重试不刷版本），理由变了则是新版本。转换**不调 AI 故不建 job**（同 `ImportSource` 本地文件路径）。确认后交给**既有** `GenerateTopic`——那条链路**零改动**。真机验收（40 条真实热点）PASS，报告 `.workbuddy/hotspot-acceptance/convert-report.md` |
| 2026-09-10 | **修既有缺陷：MCP 错误丢 stderr**（真机验收撞到）。`hotspot/mcp.py` 把 `McpClientError` 拍平成 `str(e)`，丢掉 `detail.stderr`，于是包没装时报「Server 在响应前退出」——真正原因（`ModuleNotFoundError`）就在 stderr 里。通用路径 `handlers/mcp_client.py` 早有 `_with_diagnostic`（还带 §11.3 密钥掩码），只是热点这条路没用。**上移**为 `agents/mcp_client.describe_error()`（与 `McpClientError` 同层，避免 domain→handler 倒挂），两处共用，不再有第 3 份拷贝 |
| 2026-09-13 | **修全线缺陷：Windows 注册表残留代理打死所有出站请求**。`httpx` 默认 `trust_env=True` → `urllib.request.getproxies()` 读 `HKCU\...\Internet Settings`；用户关掉代理后 `ProxyEnable=1` / `ProxyServer=127.0.0.1:7897` **不会自动清** → AI / TTS / ASR / 图像 / 下载**全线 `ConnectError`**（判据：**curl 能通但 Python 不通**）。新增 `worker/runtime/net.py::make_async_client()`：代理照继承，但 **TCP 0.25s 探测不通就当没配**（`trust_env=False`），**不一刀切**（会把真靠代理访问海外的用户打回不可用）；8 处客户端构造点全改走它，另加**结构性护栏测试**（全仓除 `net.py` 外禁止裸 `httpx.Client(` / `AsyncClient(`）。12 条新测试；`agent_created` skill `windows-stale-proxy-http` 已沉淀 |
| 2026-09-13 | **StepFun TTS 端到端打通**（S2 配音段收口）：`setx` 落 5 个用户级 env（`STEPWORK_TTS_PROVIDER` / `_API_KEY` / `_VOICE` / `_MODEL` + `STEPWORK_FFMPEG_BIN`）；探针两条不同文案各出 mp3（36.0 KB / 41.8 KB，**MD5 互异**，7.41 / 6.75 字/秒）；`SynthesizeScenes` 三幕出片 + 7.08 s 整轨。**该 key 在 PLAN 端点只有 `stepaudio-2.5-tts` 有权**，错模型返 `404 + body.type="model_invalid"`（**不是 403**）→ `_error_message` 先认 `model_invalid` 再谈状态码。报告 `.workbuddy/tts-acceptance/` |
| 2026-09-13 | **S3 状态更新为「✅ 已完成（遗留 1 项）」+ 真机验收闭环**：显式要 `illustration` 但两幕无 `image_uri` → `CreateRenderJob` 返回 `styleId=ink_text` / `degradedFrom=illustration` / `degradedReason` 指名缺失原因（不静默）；成片 h264 1080×1920 30fps 5.112 s + aac。复现脚本 `.workbuddy/s3-acceptance/s3_degrade.py`（日志 `s3_run1.log`）。**当时仍余**：A 版 `_PAPER_CSS` 回落系统楷体栈（→ **同日晚些已闭合，见下一行**） |
| 2026-09-13 | **文档对齐现状**（本次）：`ROADMAP.md` 头部日期 + `## 2. 当前状态` 改为快照表、删「下一步待启动：S1」、S3 标题改 ✅ 并按真机依据勾验收框；`COMPLETED.md` §5 登录 2026-09-13 三事并更新 S2/S3 遗留清单 |
| 2026-09-13 | **S3 最后一项遗留闭合：A 版楷体打包**（弈韬裁决）。进 **霞鹜文楷 LXGW WenKai v1.522 Regular**（OFL-1.1，**25.5 MB**，md5 `b653fcc2…`，**原样捆绑** + `LICENSE-OFL.txt`）→ `resources/fonts/lxgw-wenkai/`；`_FONT_META` 登记 `("LXGW WenKai", 400)`；`_PAPER_CSS` 字体栈首选改 `"LXGW WenKai"`（系统楷体栈降兜底）；`_STYLE_VERSION` 2→3 令旧稿缓存失效。动因：A 版是 `illustration` 的默认降级落点，此前首选系统楷体 —— Windows 落到 `simkai.ttf`，**没装楷体的机器（不少 Linux / CI 容器）一路掉到泛型 `serif` → 宋体**。判据不靠肉眼：用 CDP `CSS.getPlatformFontsForNode` 实测换前 `KaiTi`(`isCustomFont=False`) / 换后 `LXGW WenKai`(`True`) / 无楷体机器 `SimSun`（`document.fonts.check` 与「量文字宽度」两条常用判据**双双失效**，详见 `resources/fonts/README.md` 与 pitfalls）。新增**结构性护栏** `test_render_styles.py::test_every_style_prefers_a_bundled_family`（首选家族必须在已打包集合内**且**有对应 `@font-face`），三组负向验证逐条点名命中 |
| 2026-09-13 | **修既有缺陷：`font_face_css()` 的 `format()` 写死 `'truetype'`**（同日记录、同日修）。`.otf` 应为 `opentype`、`.woff2` 应为 `woff2` —— 标错时浏览器**静默拒载**，而「转 woff2 压体积」正是中文全量字体绕不开的一步，此坑必爆。修法是**把两处合成一处**：新增 `_FONT_FORMAT_BY_SUFFIX`，它**同时**是 `bundled_fonts()` 的扫描白名单与 `format()` 的取值表（此前「能扫的后缀」和「会标的后缀」各写一份，往里加 `.woff2` 会「扫到了但格式标注是错的」）。两条新测试：`test_font_face_format_follows_suffix`（`.ttf/.otf/.woff2` 三种落盘各验一次）+ `test_font_scanner_and_format_map_share_one_whitelist`（`monkeypatch` 往映射里塞 `.woff` → 断言**既扫得到也标得对**，把耦合钉死） |
| 2026-09-13 | **侧车打包打通（PyInstaller）**：新增**签入仓库的** `packaging/stepwork-worker.spec` + 重写 `scripts/build_worker_sidecar.ps1`。真机产出 `stepwork-worker.exe` **69.55 MB**（单文件，含 52 MB 字体），投递为 `apps/desktop/src-tauri/binaries/stepwork-worker-x86_64-pc-windows-msvc.exe`（Tauri `externalBin` 要的文件名）。三处实质修复：<br>① **`datas` 由代码生成而非人工清单** —— 新增 `assets.BUNDLED_DIRS` / `BUNDLED_FILES` / `bundled_datas()`，打包落点与 `repo_path()` 读取点**同源**；此前旧脚本只 `--add-data migrations`，字体 / schema / `s1_probe.html` 全没打进去（字体丢了**不报错**，只是字形悄悄变回系统字体）。<br>② **`hiddenimports` 整包收集** —— `bus.py` 用 `importlib.import_module(module_path)` 路由，模块名来自 `_ROUTES` 字典而非 import 语句，静态分析看不见那 33 个 handler；改 `collect_submodules("worker")`（228 个）而非手抄。<br>③ **不再硬依赖 `.venv`** —— 本机 `python -m venv` 建出来是空的（等效 no-op），旧脚本那句「未找到 .venv」直接把人堵死；改为候选解释器逐个探测，且「解释器不存在」与「没装 PyInstaller」**分开报**（后者给出确切 pip 命令）。PyInstaller 声明为 `[project.optional-dependencies].package`（构建期依赖，不进运行期 / 不进 dev）。<br>可选引擎（playwright / faster-whisper / edge-tts）默认**排除**，由 `STEPWORK_BUNDLE_{RENDER,ASR,TTS}` 开关 —— 关掉不是「功能缺失」而是显式边界：`_has_module()` 守卫让对应 provider 返 `None` → `UNAVAILABLE`，不会静默换 ffmpeg 渲出另一条片子 |
| 2026-09-13 | **侧车真机验收 + 冻结路径分流**：`scripts/worker_entry.py` 增 `--selfcheck`，在**冻结进程内**核对随包资源（JSON 输出，缺一条即退出码 1）—— 从外面翻归档 TOC 只能**推断**「文件在不在」，在进程内读的才是那台解释器真会读的路径。验收脚本 `.workbuddy/packaging-acceptance/accept_sidecar.py` **全部通过**：`frozen=true`、四类资源就位、**随包字体 5 个**（`Alibaba PuHuiTi` / `LXGW WenKai` / `Smiley Sans`）、迁移 SQL 28 个、`runtime.ready`（= `bootstrap_db` 已跑完）→ `health_check` → 真实命令信封 `ListWorkspaces` `ok=true`（走完 schema 校验 → bus → handler 动态导入 → SQL）→ `shutdown` 退出码 0，`startup_ms=125`。<br>另把 4 处 `Path(__file__).resolve().parents[N]` 资源查找收进新模块 `worker/runtime/assets.py`（`repo_root()` / `repo_path()`，冻结态 = `sys._MEIPASS`）：`styles._FONTS_DIR` / `bootstrap._migrations_dir()` / `envelope._SCHEMA_PATH` / `playwright.DEFAULT_DOCUMENT`。**`bootstrap` 那处原本会让侧车启动即死**（迁移目录丢了库建不起来且无兜底）。守卫：`test_assets.py` 7 条（含「调用点必须被随包清单覆盖」与「落点必须保持仓库内相对位置」两条变更检测器），三组负向验证**各自单独**命中并点名 |

**S2 剩余遗留项（2026-09-09 更新）**

1. ~~**per-request renderer hint**~~ → ✅ 已落地（见上表）
2. ~~**`background_uri` 多义**~~ → ✅ 已拆 `design_doc_uri`（见上表）
3. ~~**Playwright 依赖未声明**~~ → ✅ 已加 `[project.optional-dependencies].render`
4. **抽帧目检撞切句瞬间取空字幕** → `video_scenes.born_at_sec` 已由**渲染步骤**
   回填（第 14 条，S2 打通渲染即回填）—— 抽帧目检据此前移取样点，
   不再依赖 `__getSentBorn`
5. ✅ **`RenderSpec.style_id` 已由 Renderer 消费**（S3，2026-09-13 真机验收：
   内置 A/B 视觉稿按能力声明选型，「缺配图 → 降级 A 版」全链闭环）；
   `art_style` / `image_set_id` 仍未接入渲染路径（`image_set_id` 尚无表承载）
6. **生图 Provider 选型未定**：StepFun 生图 2026-10-10 下线，`REFERENCE.md §6` 标「未定」（通义万相 / CogView-4 / 硅基流动 / 本地 SDXL 待实测）。**image provider 接口已先落地**（`ImageProvider` 协议），厂商适配器后补
7. ✅ **TTS Provider（stepfun 复刻音色）已落地**（2026-09-09）：
   `providers/tts/stepfun.py` + `resolve_tts(kind=stepfun)` +
   设置页 `tts.voice`（复刻音色 id）+ 前端 provider 下拉补 `edge` / `stepfun`。
   **真机验收通过**（`.workbuddy/tts-acceptance/`，本机密钥 +
   `voice-tone-U9dkIJs8Ey`）：两句各出 mp3，MD5 不同（未命中错误缓存）、
   时长可实测、语速归一化生效（5.31→5.99、4.63→5.42 字/秒，目标 6.0）。
   三个踩过的坑已写进实现：① 复刻音色走 `/step_plan/v1/audio/speech`
   （少了 `step_plan` 直接 404）；② **命中错误缓存**（不同文本返回同一段
   固定音频）判据用 **MD5**（只比字节数 CBR 会撞车误报），命中即报错；
   ③ `instruction` 里写「缓慢/舒缓」会盖过 `speed`（实测差 1.4 倍），
   故按 字/秒 `atempo` 归一化（不变调；单次 0.5–2.0，超出串联）。
   **未验**：音频内容正确性需人工听一遍（本机 ASR 是假实现，不能自检）
8. ✅ **`video_scenes` 读写层已补**（repo + 3 命令 + CLI + Agent 白名单）—— 表不再空转
9. ✅ **分幕已有「生产端」调用方**（2026-09-09）：新增 `script/segment.py`
   （确定性切分纯函数）+ `jobs.lifecycle.persist_script_scenes`，
   `GenerateScript` / `SaveScript` / `EditParagraph` 三条写入路径落版即派生分幕，
   不再需要手工灌 `SaveVideoScenes`（该命令退化为「人工改幕」的覆盖入口）
10. ✅ **幕的时间轴已由配音步骤回填**（2026-09-09）：新命令 `SynthesizeScenes`，
    逐幕 TTS → **实测**时长 → 单事务回写 `audio_uri` / `duration_sec` /
    累加 `start_sec`，并默认拼整轨（可直接喂 `CreateRenderJob` 的 `user_audio`
    路径）。字幕随之从「按字符量等比分配」改为「按实测时间轴」
    （`build_srt_from_scenes`），等比分配降级为无分幕时的退路
11. ✅ **配图步骤已通**（2026-09-09）：`ImageProvider` 协议 + `resolve_image`
    + `IllustrateScenes`（含 CLI `scenes illustrate`）。接口先落地，厂商后补；
    `local` 只是**显式启用**的占位图，不是默认值
11b. ✅ **配图厂商适配器已接**（2026-09-09）：
    `providers/image/openai_compatible.py` —— 按**契约**而不是按公司切文件：
    智谱 CogView-4 / 硅基流动 / OpenAI 及任意兼容网关共用一份实现
    （`POST {base}/images/generations`），厂商差异装进 `ImagePreset` 预置表
    （base_url / model / size / **size_key**），切厂商 = 改一个 env。
    **2026-09-09 复核（比公告更早）**：StepFun `GET /v1/models` 已无任何
    文生图模型（只剩图生图 `step-image-edit-2`），实测 `step-1x-medium`
    返回 `model not supported` → 不再等 10-10，直接换道。
    **不覆盖**通义万相 / `qwen-image`：官方明确不支持 OpenAI 兼容模式
    （DashScope 原生端点，尺寸 `W*H` 星号、响应 `output.choices[0]...`）。
    解析层容忍 `data`/`images` 数组与 `b64_json`/`url`/`image_url`，
    但**取不到图必报错**；返回直链时**立刻下载落盘**（直链 10 分钟~30 天
    过期，存 url 渲片就是裂图）；错误带厂商 body 片段
12. ✅ **渲染侧已消费分幕**（2026-09-09）：`RenderSpec.scenes`（新增
    `RenderScene` 模型），`CreateRenderJob` 组装时把「有实测时长」的幕喂给
    PlaywrightRenderer —— 文本 / 配图 / 起止秒注入视觉稿，**画面按幕切换**，
    字幕也用它。时长为 0 的幕（没配音）**不喂**（画面会与音频错位）
13. ✅ **`born_at_sec` 由渲染回填**（2026-09-09）：视觉稿暴露
    `window.__getSentBorn(i)`，渲染器实测各幕首句出现秒 → 落回
    `video_scenes.born_at_sec`。长度严格对齐才写（半截列表会让某一幕默默
    前移错位的秒数，比不写危险）。抽帧目检撞切句瞬间据此前移取样点
14. ✅ **S2「端到端一条流水线」已闭合**：选题→文案→（确定性）分幕→逐幕
    配音（实测时间轴）→逐幕配图（接口就绪）→按幕渲染，字幕按实测时间轴。
    分幕不再是孤岛，每一段都有「生产端」调用方（无死挂点）

**S2 收尾 / S3 遗留**（见 `ROADMAP.md` §S3）：前端仍无分幕 UI（P4 缺口，S6 补）；
`RenderSpec.style_id` 已接入渲染并**真机验收**（2026-09-13，含「缺配图 → 降级 A 版」
链路），`art_style` / `image_set_id` 仍未接入渲染路径；
`drawtext` 版 FFmpegRenderer 仍吃整段文本（保持原样）；
**插画版真实生图出片验收**（适配器就绪但本机无生图密钥，待选厂商 + 配密钥）。
S3 的字体项已于 2026-09-13 闭合（霞鹜文楷进 `resources/fonts/`，A 版不再依赖系统楷体）
