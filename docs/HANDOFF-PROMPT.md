# STEPWORK HANDOFF PROMPT — 交给编码 Agent 的执行提示词

> **用法**：整段复制到新的开发会话（任一 Agent），它会自行读完上下文并开始执行当前阶段任务。
> **当前阶段**：S1 · Playwright 渲染器探路
> **Date:** 2026-09-08

---

## 你要开发的产品

**STEPWORK** —— 一间 Agent 原生的短视频创作工厂：从选题开始，到成片结束。

- **仓库**：`D:\Code\StepWork`（main @ bb31569，136 commits，AGPL-3.0-or-later）
- **技术栈**：Tauri 2 + React 18 + Vite 5（桌面端） + Python 3.12 worker sidecar + SQLite(WAL)
- **流水线**：发现选题 → 文案 → 配音 → 配图 → 渲染 → 发布
- **北极星**：一个人，一条指令，出一條成片（端到端 ≤ 15 分钟，人工决策 ≤ 2 次）

### 你在生态中的位置

你是 `YT-Agent-Ontology`（private 仓库 `github.com/ra1nzzz/YT-Agent-Ontology`）中 **ContentOps 域的 Owner**：
素材（Material）/ 脚本（Script）/ 视频草稿（Draft）/ 发布（Publish）/ 渠道（Channel）。

---

## 开工前必读（按顺序）

```text
1. D:\Code\StepWork\docs\REPOSITIONING.md   ← 定位、六项不可违背原则、目标架构
2. D:\Code\StepWork\docs\ROADMAP.md         ← 北极星、在办任务（你是 S1）、S0-S8 路线
3. D:\Code\StepWork\docs\COMPLETED.md       ← 已有什么；⛔ 假实现与空壳目录必看
4. D:\Code\StepWork\docs\REFERENCE.md       ← 复用来源与授权
```

---

## 代码现状（⚠️ 别被目录名骗，这些是硬事实）

### 空壳目录（只有 `.gitkeep`，无代码）

```text
core/{domain,commands,application,events,policies}
worker/{tasks,providers,media}
sdk/{python,typescript,plugin,agent-adapter}
publisher-engine/{browser,dom,rpc,runtime,uploader}
plugins/{official,registry}
```

**真实代码全部在 `worker/runtime/`**：120 个 py 文件，16421 行。
领域模型在 `worker/runtime/models.py`（pydantic v2），Provider 在 `worker/runtime/providers/`，渲染在 `worker/runtime/render/`。

### ⛔ 假实现（跑得通但产出为空，别信）

| 位置 | 真相 |
|---|---|
| `worker/runtime/providers/asr/local.py` | 硬编码 5 行中文假台词，按 URI 哈希轮转 |
| `worker/runtime/providers/tts/local.py` | 静音 WAV，只按字数算真实时长 |

不装可选依赖 `.[asr]` / `.[tts]` 时，链路会「成功」但产出为空。

### ✅ 可直接依赖的真实现

命令总线 `commands/bus.py`（~90 路由，加命令 = 加一行路由 + 一个 handler 文件）·
任务状态机 `jobs/`（8 态 × 11 阶段 + lease/heartbeat/取消）·
Provider 协议 `providers/*/base.py`（PEP 544 Protocol）·
LLM `ai/cloud.py` · 选题 `topic/` · 脚本 `script/` · 品牌档 `handlers/brand.py` ·
SRT `render/subtitles.py` · OTIO/EDL `render/edit_export.py` ·
契约防漂移 `results/registry.py` + `scripts/gen_result_types.py` ·
Agent 互操作 `mcp/server.py`、`agents/{mcp_client,a2a_*,acp_client}.py`。

---

## 不得自行重新定义（Ontology 约束）

以下概念已在 `YT-Agent-Ontology` 定死，**不得另立一套**：
`Identity` · `Agent` · `Task` · `Session` · `Workflow`（Definition/Execution 分离）·
`Skill` · `Capability` · `Memory` · `Knowledge` · `Event`（信封）· `Artifact` · `Action` · `Approval`

也**不得自建** Workflow Engine / Knowledge Engine（消费生态能力）。

STEPWORK 独有的概念（素材/脚本/草稿/发布/渠道）放在 **ContentOps 域**，不要污染 Core。

---

## 必须保持兼容（禁止 breaking change）

- 现有 IPC 通道名与 JSON-RPC 帧格式（4 字节长度前缀，`MAX_FRAME_SIZE=1MiB`）
- 命令总线现有 ~90 条路由的命令名与响应契约
- 现有 22 张表结构（新增走 `migrations/0012+`，不改既有表）
- 现有 `FFmpegRenderer`（新增 Playwright renderer，不删除旧的）
- `results/registry.py` 的契约注册与 `gen_result_types.py` 的 CI `--check`

**迁移原则**：兼容 → 映射 → 迁移 → 删除。**禁止大爆炸式重写。**

---

## 当前阶段任务：S1 · Playwright 渲染器探路

> **目标**：证明「Playwright 逐帧渲染」能在现有 Job / 进度 / 取消框架里正常工作。
> 这是最大未知数。通过之后，S2–S8 都是照模式复制。

### 要做的事

1. **新增** `worker/runtime/providers/renderer/playwright.py`，实现 `RendererProvider` 协议：

   ```python
   class RendererProvider(Protocol):
       name: str
       capability: str
       def render(self, spec: RenderSpec, audio_uri: str,
                  progress_cb: Callable[[float], None],
                  cancel_event: Any) -> RenderResult: ...
   ```

   （协议定义在 `worker/runtime/providers/renderer/base.py`，照抄即可，不要改它）

2. **`providers/resolve.py`** 增加 `resolve_renderer` 的 `STEPWORK_RENDER_PROVIDER=playwright` 分支（保持 `ffmpeg` 为默认）。

3. **搬运已验证的渲染实现**：从
   `C:/Users/my/WorkBuddy/2026-09-07-05-23-14/gender-video/scripts/render.py`
   （Playwright 逐帧 + ffmpeg 管道直连，已出 3 条成片，支持 `--html` / `--test N`）
   抽取逻辑，适配到协议签名。同步搬运 `still_*.py` 抽帧目检脚本。

4. **`RenderSpec`** 扩字段（`worker/runtime/models.py`，加默认值，向后兼容）：
   `style_id: str = "illustration"`、`art_style: str = "xiaohei"`、`image_set_id: str | None = None`

### 验收标准（全部通过才算完成）

- [ ] 用现成素材渲出一条 **30 秒 9:16 成片**，1080×1920，H.264 + AAC
- [ ] `progress_cb` 能驱动前端进度条（不是假进度）
- [ ] 中途取消无僵尸进程（复用 `render/ffmpeg_runner.py` 的取消语义）
- [ ] 原 `FFmpegRenderer` 仍可正常调用（未破坏现有路径）
- [ ] `mypy strict` + `ruff` + `pytest -m "not perf"` 全绿
- [ ] 新增至少 3 条测试覆盖：正常渲染 / 取消 / ffmpeg 不可用时抛 `FFmpegUnavailable`

### 当前阶段禁止修改

❌ `render/templates.py` 的模板结构（S3 才动）
❌ `commands/bus.py` 现有路由（只加不改）
❌ 任何既有表结构
❌ 前端页面结构
❌ 删除或重写 `FFmpegRenderer`

---

## 已知坑（照做，别重踩）

| 坑 | 处理 |
|---|---|
| **Playwright 逐帧必须管道直连 ffmpeg** | 本机装不上 Remotion；用 Playwright 截图 → pipe 给 ffmpeg，别落地中间帧堆 |
| **并行编辑同一文件会互相覆盖** | 串行编辑，改完立即 grep 校验 |
| **TTS 情绪指令会盖过 `speed`** | 生成后用 `atempo` 归一化，别指望参数 |
| **音频缓存判重必须 MD5 + 字节数双判据** | CBR 编码会撞字节数，只比大小会误判 |
| **抽帧撞切句瞬间会取到空字幕** | 取样前判断若 `born > t - 0.45` 则前移 |
| **生图并发会撞文件名覆盖** | 用「序号 + uuid」命名，不依赖时间戳 |
| **StepFun 生图 2026-10-10 下线** | 图像层必须可插拔，别绑死；配图内不生成中文文字（字形不可靠），文字在 HTML 层叠加 |
| **git 操作一律 `env -u NODE_OPTIONS git ...`** | NODE_OPTIONS 注入的 safe-delete shim 会劫持 `fs.unlink/rmdir`，曾删掉 `.git/refs` 与整个目录 |

---

## 完成后必须输出（9 项验收报告）

1. **改了哪些文件**（路径清单）
2. **新增了哪些测试**、覆盖范围
3. **验证结果**（成片路径 + ffprobe 规格输出）
4. **兼容性结论**：现有路径是否仍可用，有无 breaking change
5. **Ontology 对齐情况**：新增概念是否落入 ContentOps 域，有无污染 Core
6. **遇到的问题与解法**（供后续阶段参考）
7. **文档更新**：`ROADMAP.md`（S1 打勾）、`COMPLETED.md`（新增条目）、`REFERENCE.md`（新依赖登记）
8. **下一步建议**（S2 的第一件事）
9. **未完成/存疑项**：明确列出，不要含糊带过

---

## 判断本阶段完成的标准

上述 9 项报告齐备 + 验收标准全部打勾 + CI 全绿 + 文档已回写。

**不要顺手做 S2 的事**（image provider、`video_scenes` 表、TTS provider）——先把 S1 的未知数验证干净。
