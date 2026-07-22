# STEPWORK W6 执行计划（YTDEV 原子并发）

**版本：v1.0**
**日期：2026-07-22**
**上位文档**：STRATEGY_PLAN.md §3.3（Week 6 视频草稿渲染）、SYSTEM_SPEC §7.2/§10.4/§12（Renderer 插件、取消、FFmpeg 受控）、ADR-009（插件独立进程）、W3_W4_PLAN.md（复用基座）
**范式**：YTDEV（7 阶段：调研→PLAN→头脑风暴→终稿→并发实现→三重 REVIEW→原子提交）

---

## 0. 范围现实（Stage 1 调研结论）

| 已具备（W2/W3/W4 产物） | W6 需建 |
|---|---|
| Job 引擎（lease/heartbeat/sweep/retry） | RenderJob handler（复用引擎，不重写） |
| Command Bus（envelope 校验 + 懒加载路由） | 新增 `CreateRenderJob` 路由 + 渲染 handler |
| Repos（workspaces/projects/source_assets/jobs/content_versions） | `content_versions` 承载 `video_draft`，**无新 migration** |
| ASR/AI Provider 协议范式（Protocol + 内置实现 + 云骨架 + env 接线） | RendererProvider / TTSProvider 同范式的两个新协议 |
| Rust `dispatch_command` 单一桥接 | **直接复用，W6 不新增任何 Rust 命令** |
| `JobStage.RENDERING` 已存在于 models.py | 渲染阶段直接用 |

**关键路径**：W1→W2→W3-W4→W5 脚本→**W6 渲染**→W9 RC1。用户明确"执行 W6"，**跳过 W5（脚本编辑器 UI 未做）**。

### 范围决策（必须记录）
- **渲染输入不依赖 W5**：W6 的 RenderJob 消费 W3/W4 已落库的 `ContentVersion`（`content_type` 为 `transcript` 或后续 `script` 文本），直接喂给 TTS + 9:16 模板。W5 编辑器仅是 UI 录入入口，不影响渲染管线本身。
- **Renderer 是插件类型（SYSTEM_SPEC §12.1）**：W6 交付"接口雏形"——定义 `RendererProvider` 协议 + 一个**内置** `FFmpegRenderer`（capability `render:vertical-caption-v1`）。完整"独立进程插件运行时"属 V0.2（ADR-009），W6 仅接口 + 内置实现，协议设计对齐 ADR-009（凭据经受控 RPC、输出经 Schema 校验）。
- **不新增 DB 表/migration**：渲染产物存 `content_versions(content_type="video_draft", content=JSON 元数据含磁盘 uri)`，落在既有版本链（parent = 源版本）。
- **FFmpeg 受控外部二进制（§10.4 / SYSTEM_SPEC 行 1080）**：路径来自配置白名单或 `PATH`；**缺失即 `UNAVAILABLE`，绝不伪造渲染结果**。

---

## 1. 模块编号体系（W6 前缀）

```
L.14 models 扩展      worker/runtime/models.py（RenderSpec/RenderResult/TTSConfig/VideoDraftMeta + commandType 枚举扩展）
L.15 Renderer 协议    worker/runtime/providers/renderer/{base,ffmpeg}.py
L.16 TTS 协议         worker/runtime/providers/tts/{base,local,cloud}.py
L.17 FFmpeg 受控封装  worker/runtime/render/ffmpeg_runner.py（可终止 / 进度 / 取消后 0 僵尸）
L.18 渲染 handler      worker/runtime/handlers/render_source.py（建 RenderJob→TTS→渲染→落库→set_result）
L.19 前端 W6          apps/desktop/src/features/render/ + stores/useRenderStore.ts（复用 dispatch_command）
```

---

## 2. 接口签名（核心契约）

```python
# L.14 models
class TTSEngine(str, Enum):
    SYNTHESIZE = "synthesize"      # 用 TTS 合成旁白
    USER_AUDIO = "user_audio"       # 用户录音，直接使用、不重合成

@dataclass
class RenderSpec:
    source_version_id: str           # 消费 ContentVersion（transcript/script 文本）
    template: str = "vertical-caption-v1"
    tts_engine: TTSEngine = SYNTHESIZE
    tts_provider: str | None = None # 云 TTS hint（per-request，零硬编码）
    user_audio_uri: str | None = None
    background_uri: str | None = None
    resolution: tuple[int, int] = (1080, 1920)
    fps: int = 30

@dataclass
class RenderResult:
    video_uri: str
    duration_seconds: float
    template: str
    tts_engine: str

# L.15 RendererProvider（对齐 ADR-009 插件协议）
class RendererProvider(Protocol):
    name: str
    capability: str                      # "render:vertical-caption-v1"
    def render(self, spec: RenderSpec,
               progress_cb: Callable[[float], None],
               cancel_event: Any) -> RenderResult

# L.16 TTSProvider
class TTSProvider(Protocol):
    name: str
    async def synthesize(self, text: str, opts: dict) -> str  # 返回音频 uri

# L.17 FFmpegRunner（受控外部二进制）
class FFmpegRunner:
    def __init__(self, bin_path: str | None = None)  # None→搜 PATH；缺失即 UNAVAILABLE
    def run(self, args: list[str], progress_cb, cancel_event,
            timeout_sec: int = 600) -> int               # 返回退出码；cancel→terminate 进程树
    # 进度：解析 stderr 的 frame= / time= 行 → 比例
```

---

## 3. Batch 编排（复用基座、无交叉）

```
Batch 0（主代理手写，L.14-L.18 后端）
    → pytest：FFmpegRunner 终止/0 僵尸（mock 子进程）、TTS 本地确定性、
      render handler 端到端（LocalTTS + FFmpeg 缺失走 UNAVAILABLE）、
      job 取消→CANCELLED
Batch 1（前端，L.19）
    → tsc --noEmit：RenderView（选源版本/模板/TTS 引擎、阶段·进度·取消·重试，
      对齐 PRD §338）+ types/tauri 复用 dispatch_command
Batch 2（收口）
    → 写 docs/W6_REVIEW.md（三重 REVIEW ≥8）；原子提交；push -u origin main
```

**共享文件**（主代理统一改）：`worker/runtime/models.py`（扩 commandType/meta）、`schemas/command-envelope.schema.json`（枚举加 `CreateRenderJob`）、`worker/runtime/commands/bus.py`（路由）、`apps/desktop/src/lib/types.ts` + `tauri.ts`（复用 dispatch_command）、`docs/W6_REVIEW.md`。

---

## 4. 三角色头脑风暴补丁（P0/P1）

| 角色 | 发现 | 级别 | 合入 |
|---|---|---|---|
| 架构师 | RenderJob **必须复用 W2 job 引擎**（lease/heartbeat/sweep），与 import/transcribe/analyze 同构；cancel 必须 terminate ffmpeg 子进程（§10.4） | P0 | L.17/L.18 |
| 安全健壮性 | FFmpeg 受控外部二进制：路径来自配置白名单/ PATH，**用 argv list 非 shell 拼接**，禁止用户路径进 shell；云 TTS 零硬编码密钥（env） | P0 | L.16/L.17 |
| UX | 渲染 UI 展示 阶段 / 进度 / 取消 / 失败原因（PRD §338），与 Import/Transcript/Analysis 视图同构 | P0 | L.19 |
| UX | 失败可重试（复用 `retry_eligible`）；模板 + TTS 引擎切换入口 | P1 | L.19 |
| 架构师 | 渲染输出存 `content_versions(video_draft)` 不新增表；FFmpeg 缺失即 `UNAVAILABLE` 不伪造 | P1 | L.17/L.18 |

---

## 5. 验证矩阵（质量门禁）

| 层 | 命令 | 通过线 |
|---|---|---|
| Python | `pytest worker/tests -q` | 全绿（含 FFmpegRunner 终止/0 僵尸、TTS 本地确定性、render 端到端、job 取消） |
| Python | `mypy worker/`（strict） | 0 issue |
| Python | `ruff check worker/` | clean |
| 前端 | `tsc --noEmit` | exit 0 |
| Rust | `cargo check`（仅复用既有 `dispatch_command`，W6 无新 Rust 代码） | 随 E43us7 后台验证；若通过则 W6 无需新 Rust 验证 |

**STRATEGY Gate 说明**：
- "10 个连续渲染无崩溃，取消后 0 僵尸进程" 需真实 ffmpeg + 媒体 → 标记 **手动 / CI-with-secrets**；本地用 **mock ffmpeg 子进程**验证"cancel → terminate → 0 zombie"逻辑与退出码。
- 真实 TTS API 需密钥 → 云 TTS 走 env 接线 + 单测 mock；本地 TTS 用确定性占位（生成静音/占位音频）满足"可运行"证伪。
- 不伪造真实通过率。

---

## 6. 风险与遗留

- **429 限流**：子代理可能打断 → Batch 若失败主代理手写补齐（沿用 W3-W4 实战）。
- **本机无 ffmpeg**：本地仅验证逻辑（mock 子进程 + 缺失走 UNAVAILABLE），真实渲染手动/CI。
- **缺少 MSVC link.exe**：完整 `tauri build` 不行，但 `cargo check` 可跑（不链接）；W6 无新 Rust 代码，复用 W3-W4 桥接。
- **cargo check（E43us7）后台**：用户指令——**check 结果回传优先处理，暂停 W6**。

---

## 7. 下一步（本会话内）

1. 写本 PLAN（已提交为抗中断交付物）。
2. Batch 0：models 扩展 + Renderer/TTS Provider + FFmpegRunner + render_source handler + 单测 → ruff/mypy/pytest 全绿 → 原子提交。
3. Batch 1：前端 RenderView → tsc 全绿 → 原子提交。
4. Batch 2：W6_REVIEW（三重 REVIEW）+ 原子提交 + push。
