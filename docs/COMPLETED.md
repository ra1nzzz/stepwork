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
