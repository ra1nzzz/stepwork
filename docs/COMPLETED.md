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

**S2 剩余遗留项（2026-09-09 更新）**

1. ~~**per-request renderer hint**~~ → ✅ 已落地（见上表）
2. ~~**`background_uri` 多义**~~ → ✅ 已拆 `design_doc_uri`（见上表）
3. ~~**Playwright 依赖未声明**~~ → ✅ 已加 `[project.optional-dependencies].render`
4. **抽帧目检撞切句瞬间取空字幕** → `video_scenes.born_at_sec` 列已建；幕已自动派生（第 8 条），待 TTS 步骤回填该列，目检脚本改读列（不再依赖 `__getSentBorn`）
5. **`RenderSpec.style_id` / `art_style` / `image_set_id` 仍未被 Renderer 消费** —— S3 模板层按能力声明（`{image}` / `{}`）选型时接通
6. **生图 Provider 选型未定**：StepFun 生图 2026-10-10 下线，`REFERENCE.md §6` 标「未定」（通义万相 / CogView-4 / 硅基流动 / 本地 SDXL 待实测）。**S2 的 image provider 接口应先于厂商实现落地**，别把接口绑死在某家
7. **TTS Provider（stepfun 复刻音色）未做**：`atempo` 语速归一化 + MD5&字节数双判据缓存均已验证，照搬即可
8. ✅ **`video_scenes` 读写层已补**（repo + 3 命令 + CLI + Agent 白名单）—— 表不再空转
9. ✅ **分幕已有「生产端」调用方**（2026-09-09）：新增 `script/segment.py`
   （确定性切分纯函数）+ `jobs.lifecycle.persist_script_scenes`，
   `GenerateScript` / `SaveScript` / `EditParagraph` 三条写入路径落版即派生分幕，
   不再需要手工灌 `SaveVideoScenes`（该命令退化为「人工改幕」的覆盖入口）
10. ⚠️ **幕的 `start_sec` 仍是 0**：分幕此刻只有文本，时间轴要等 TTS 实测
    `duration_sec` 后累加得出 —— 与第 7 条一起做（配音步骤回填 `audio_uri`
    + `duration_sec`，再顺推 `start_sec`）
11. ⚠️ **前端无分幕 UI**：`types.ts` 的 union 已同步（防漂移测试会拦），但没有任何页面
    调用这三个命令。按 P4，GUI 侧至少要有一个入口（S2 尾段或 S6 补齐）
