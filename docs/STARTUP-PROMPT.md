# STEPWORK 启动提示词（新会话直接粘贴这段）

> 用法：整段复制到新会话发送。它自带全部硬约束，Agent 读完会自行加载仓库上下文并开始 S1。

---

你要接手开发 **STEPWORK** —— 一间 Agent 原生的**短视频创作工厂**：从选题开始，到成片结束。

## 0. 先读这些（按顺序，别跳）

```text
D:\Code\StepWork\docs\HANDOFF-PROMPT.md   ← 执行提示词全文（S1 任务、验收、禁止项）
D:\Code\StepWork\docs\README.md           ← 知识库索引与治理规则
D:\Code\StepWork\docs\REPOSITIONING.md    ← 定位、六项不可违背原则、目标架构
D:\Code\StepWork\docs\ROADMAP.md          ← 北极星、在办任务、S0-S8 路线（你做 S1）
D:\Code\StepWork\docs\COMPLETED.md        ← 已有什么；⛔ 假实现与空壳目录必看
D:\Code\StepWork\docs\REFERENCE.md        ← 复用来源与授权，§2.1 文件级借鉴清单
```

仓库：`D:\Code\StepWork`（main @ `b0b2244`，AGPL-3.0-or-later，136 commits 全为 ra1nzzz 一人）
技术栈：Tauri 2 + React 18 + Vite 5 桌面端 / Python 3.12 worker sidecar / SQLite(WAL)
流水线：**发现选题 → 定角度 → 文案 → 配音 → 配图 → 渲染 → 发布**
北极星：一个人，一条指令，出一条成片（端到端 ≤ 15 分钟，人工决策 ≤ 2 次）
未推送：本地 `236a595`（文档重定位）、`b0b2244`（授权修正）两个提交尚未 push

## 1. 六项不可违背原则

1. **Agent 原生，双向** —— 能操作其它 Agent，也能被其它 Agent 操作
2. **GUI 与 CLI 同为一等公民** —— 任何能力先有 CLI / 命令总线入口，GUI 只是它的一层皮
3. **Ontology 为准** —— 以 `YT-Agent-Ontology`（private，`github.com/ra1nzzz/YT-Agent-Ontology`）统一语义，不建生态壁垒。STEPWORK 是 **ContentOps 域 Owner**（素材/脚本/视频草稿/发布/渠道），不得污染 Core
4. **`/docs` 即项目知识库** —— 完成一项从 `ROADMAP.md` 移入 `COMPLETED.md`，引入外部来源立即登记 `REFERENCE.md`，三份职责禁止重合，用 `consolidate-project-knowledge-base` 治理防漂移
5. **优先复用自研 / MIT** —— 不自研已有的轮子；授权不允许时借鉴原理，不抄代码
6. **继续 AGPL** —— 接受第一个外部 PR 前必须定下 CLA 或双许可，否则永久锁死

## 2. 代码现状（硬事实，别被目录名骗）

**空壳目录（只有 `.gitkeep`）**：`core/{domain,commands,application,events,policies}`、`worker/{tasks,providers,media}`、`sdk/{python,typescript,plugin,agent-adapter}`、`publisher-engine/*`、`plugins/{official,registry}`。
**真实代码全在 `worker/runtime/`**：120 个 py、16421 行。模型在 `models.py`，Provider 在 `providers/`，渲染在 `render/`。

**⛔ 假实现（跑得通但产出为空，别拿它估工期）**：`providers/asr/local.py` 是硬编码 5 行假台词按哈希轮转；`providers/tts/local.py` 产静音 WAV。不装可选依赖时链路「成功」但产出为空。

**✅ 可直接依赖**：命令总线 `commands/bus.py`（~90 路由）、任务状态机 `jobs/`（8 态 × 11 阶段 + lease/取消）、Provider 协议 `providers/*/base.py`（PEP 544 Protocol）、LLM `ai/cloud.py`、选题 `topic/`、脚本 `script/`、品牌档 `handlers/brand.py`、SRT `render/subtitles.py`、OTIO/EDL `render/edit_export.py`、契约防漂移 `results/registry.py` + `gen_result_types.py`、Agent 互操作 `mcp/server.py` + `agents/`。

## 3. 可复用资产：归属与授权（2026-09-08 核实，别搞反）

| 仓库 | 归属 | 授权 | 方式 | 借鉴要点 |
|---|---|---|---|---|
| `OrchClaw-Lite` | ✅ 自研 | MIT | **直接复用** | `src/protocol.js` Agent 消息协议（11 类型 + 校验 + `capabilities[]`）；`src/task-state-machine.js` 转移表（含 `submitted`/`returned`）；三维评审 + 结项报告 |
| `model-router` | ✅ 自研 | MIT | **直接复用** | `models.json` 任务→模型路由 `primary+fallbacks[]`；成本优先；`modelErrorTracker`（5min/3 次错误切 fallback）+ 超时重试。STEPWORK 目前**无 LLM 降级** |
| `huashu-design` | ❌ fork `alchaincyf/huashu-design` | MIT | **仅借鉴方法论** | 三方向硬门；**事实验证先于假设**（S5 选题必用）；`voiceover-pipeline.md`「先解说词、按音频实测时长驱动画面」；`render-video-seek.js` 按时间轴 seek 直录；`ai-video-review.md` 终渲喂视觉模型评审（带 `--context`） |
| `douyin-live-info` | ❌ fork `qq564118922/douyin-live-info` | 🚨 **CC BY-NC 4.0** | ⛔ **仅借鉴原理，禁止引入代码** | 弹幕=热点信号源；采集骨架 `subscribe→heartbeat→decompress→parse→normalize`；消息归一化保留 `raw`；SQLite 落盘 + 导出 |

⛔ 红线：`douyin-live-info` 禁止商用，不得复制/改写/分发其任何源码与资源（含 protobuf 定义、签名算法）。

## 4. 当前任务：S1 · Playwright 渲染器探路

**目标**：证明「Playwright 逐帧渲染」能在现有 Job / 进度 / 取消框架里正常工作。这是最大未知数，通过之后 S2–S8 都是照模式复制。

1. 新增 `worker/runtime/providers/renderer/playwright.py`，实现 `RendererProvider` 协议（定义在 `providers/renderer/base.py`，**照抄，不要改协议**）
2. `providers/resolve.py` 增加 `STEPWORK_RENDER_PROVIDER=playwright` 分支（保持 `ffmpeg` 为默认）
3. 从 `C:/Users/my/WorkBuddy/2026-09-07-05-23-14/gender-video/scripts/render.py` 搬运已验证实现（Playwright 逐帧 + ffmpeg 管道直连，已出 3 条成片），同步搬 `still_*.py` 抽帧目检
4. `RenderSpec` 加默认值字段：`style_id`、`art_style`、`image_set_id`

**验收**：30 秒 9:16 成片 1080×1920 H.264+AAC ｜ `progress_cb` 真驱动进度 ｜ 取消无僵尸进程 ｜ 原 `FFmpegRenderer` 仍可用 ｜ mypy/ruff/pytest 全绿 ｜ 至少 3 条测试（正常/取消/ffmpeg 不可用）

**禁止动**：`render/templates.py` 模板结构（S3 才动）、`commands/bus.py` 现有路由（只加不改）、既有表结构、前端页面、删除 `FFmpegRenderer`。
**不要顺手做 S2**（image provider、`video_scenes` 表、TTS provider）。

## 5. 已知坑（照做，别重踩）

| 坑 | 处理 |
|---|---|
| git 一律 `env -u NODE_OPTIONS git ...` | safe-delete shim 会劫持 `fs.unlink/rmdir`，曾删掉 `.git/refs` 与整个目录 |
| 禁止 `git stash` | 同一 shim 曾因此报废仓库。基线对比用临时目录 + `git show HEAD:<path>` |
| git 身份已全局配置 | `ra1nzzz <ra1nzzz@users.noreply.github.com>`，**不要**再写 `-c user.name=...` |
| GitHub 走直连 | 代理不可用，用 `env -u NODE_OPTIONS -u http_proxy -u https_proxy ...`，失败率高需重试循环 |
| Playwright 逐帧必须管道直连 ffmpeg | 本机装不上 Remotion；截图 → pipe 给 ffmpeg，别落地中间帧堆 |
| 并行编辑同一文件会互相覆盖 | 串行编辑，改完立即 grep 校验 |
| TTS 情绪指令盖过 `speed` | 生成后 `atempo` 归一化 |
| 音频缓存判重需 MD5 + 字节数双判据 | CBR 会撞字节数 |
| 抽帧撞切句瞬间取到空字幕 | 取样前若 `born > t - 0.45` 则前移 |
| 生图并发撞文件名覆盖 | 用「序号 + uuid」，不依赖时间戳 |
| StepFun 生图 2026-10-10 下线 | 图像层必须可插拔；配图内不生成中文文字，文字在 HTML 层叠加 |

## 6. 完成后输出（9 项验收报告）

改了哪些文件 ｜ 新增测试与覆盖 ｜ 验证结果（成片路径 + ffprobe 规格）｜ 兼容性结论 ｜ Ontology 对齐情况（是否污染 Core）｜ 问题与解法 ｜ 文档更新（ROADMAP 打勾 / COMPLETED 新增 / REFERENCE 登记，并跑一次知识库漂移检查）｜ 下一步建议（S2 第一件事）｜ 未完成与存疑项（明确列出，不含糊）

**完成标准**：9 项报告齐备 + 验收全打勾 + CI 全绿 + 文档已回写。
