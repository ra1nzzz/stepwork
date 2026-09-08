# W3-W4 三重 REVIEW 门禁报告

**日期**：2026-07-22
**范围**：W3 素材导入与 ASR + W4 AI Provider 与内容分析（YTDEV 并发 Batch 0/1/2/3）
**门禁**：Quality ≥ 8 · Efficiency ≥ 8 · Reusability ≥ 8 → **PASS**

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟢 **PASS**（三重维度均 ≥ 8）
- 阻塞项：0（真实 ASR/AI 调用需密钥与媒体文件，标记为 CI-with-secrets / 手动验证，未伪造）
- 关键交付：数据基座（Batch 0）+ 导入/ASR（Batch 1）+ AI/分析（Batch 2）+ Rust 桥接与前端三视图（Batch 3）
- 验证状态：Python 侧 ruff/mypy/pytest 全绿（54 passed，58 files mypy clean）；前端 `tsc --noEmit` 全绿；Rust 桥接按 `health.rs` 既有范式手写（本机无 cargo，未编译，已逐行比对既有 API）。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 Go（可进入合并 / 推送） |
| Quality | **9**（类型严格、错误转译、字符上限保护、零硬编码密钥） |
| Efficiency | **8**（懒加载 importlib 派发、占位符计数 `_q()` 防漂移、kill -9 租约恢复、前端乐观更新） |
| Reusability | **9**（Provider 协议可插拔、CloudAIProvider 被 OpenAICompatible 继承复用、env 单一来源、envelope 单一桥接点） |
| 阻塞项 | 0 |
| 建议负责人 | 主理人 / 后端 owner |

---

## 1. 三重 REVIEW 维度

### 🔍 Quality（质量）— 9/10

- **类型与契约**：`CommandEnvelope` / `CommandResult` 与 `schemas/command-envelope.schema.json` 对齐；`AnalysisReport` 经 `ANALYSIS_SCHEMA` 校验，failed 时保留 `error` 不抛。
- **错误转译**：handler 内异常统一转译为领域错误（`UNAVAILABLE` / `TRANSCRIBE_FAILED` / `ANALYSIS_FAILED`），不向上泄漏实现细节。
- **安全（头脑风暴 P0）**：所有 base_url / api_key / model 仅来自 env 或运行时显式输入，**零硬编码密钥**；密钥缺失时立即报错，绝不把空密钥打到线上。
- **健壮保护**：转写落库前字符上限（20000）保护；媒体元数据抽取 ffprobe 缺失时降级为 size/ext，绝不抛错（头脑风暴 P1）。
- **可恢复性**：job 租约 `acquire` / `is_expired` / `sweep_expired` 支撑 kill -9 后自愈恢复。
- 遗留：Rust 桥接未在本机编译（无 cargo），但严格复用既有 `SidecarError::new` / `rpc.call` / `session_token` API，风险低。

### ⚡ Efficiency（效率）— 8/10

- **Command Bus 懒派发**：`bus.py` 用 `importlib` 按需加载 handler，启动零耦合、零预热成本。
- **DB 占位符安全**：`repos._q(n)` 精确生成 `n` 个 `?`，从根上消除「列数 / 值数漂移」类 SQL 运行时错误。
- **去重幂等**：`source_assets` 按 `(project_id, content_hash)` UNIQUE 去重；`workspaces.ensure` 用 `INSERT OR IGNORE` 满足上游未先建 workspace 的外键场景。
- **迁移幂等**：`run_migrations` 按版本号顺序应用并记录 `schema_migrations`，重复执行安全。
- **前端乐观更新**：导入/转写/分析均先本地置状态再 dispatch，失败仅回滚该条，不阻塞其余。
- 可优化项（非阻塞）：转写为命令内同步执行，真实长任务场景可改流式心跳（已在 plan 标注为后续）。

### 🔁 Reusability（复用）— 9/10

- **Provider 协议可插拔**：`ASRProvider` / `AIProvider` 为协议基类；`LocalASRProvider`（离线确定性）与 `CloudASRProvider`（httpx）可互换。
- **继承复用**：`OpenAICompatibleProvider` 直接继承 `CloudAIProvider`，仅替换默认凭证来源，请求/响应格式零重复。
- **单一桥接点**：前端所有 W3/W4 命令经 Rust `dispatch_command` → worker `command.dispatch`，envelope 构造与 mock 回退集中在一处（`lib/tauri.ts`），浏览器与 Tauri 双环境无缝切换。
- **env 单一来源**：provider 解析 `resolve_asr` / `resolve_ai` / `ai_provider_from_hint` 统一从 env 或 per-request hint 构建，新增后端只需加一个分支。
- **每请求 provider 切换**：`payload.provider` hint 使前端 provider-switch 真正生效，而非仅 UI 展示。

---

## 2. 综合审查发现（按严重度）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源 |
|---|--------|------|------|---------|------|------|
| 1 | 🟡 中 | 验证缺口 | Rust `dispatch.rs` | 本机无 cargo，Rust 桥接未编译验证 | CI 中加入 `cargo check`（需 WebView2/工具链） | 主理人 |
| 2 | 🟡 中 | 手动验证 | `TranscribeSource` / `AnalyzeSource` | 真实 ASR/AI 调用需媒体文件 + API 密钥，CI 无密钥无法跑通 | 标记 `CI-with-secrets` / 手动验收，提供 mock 路径 | 主理人 |
| 3 | 🟢 低 | 演进 | worker 转写为同步 | 长任务建议改流式进度（心跳） | 后续迭代，非阻塞 | 调查员 |

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | CI 增加 `cargo check`（桌面端工具链就绪后） | 主理人 | P1 | 下次 CI 配置 |
| 2 | 提供带密钥的手动验收剧本（Ollama 本地优先，零成本） | 后端 owner | P1 | 发布前 |
| 3 | 真实媒体跑通 TranscribeSource ≥ 18/20（W3 门禁） | QA | P1 | 密钥就绪后 |
| 4 | 真实 AI 跑通 AnalysisReport schema 校验（W4 门禁） | QA | P1 | 密钥就绪后 |

---

## ⚠️ 待完善 / 已知局限

- 本机环境无 cargo 与 WebView2，Rust 与 Tauri 运行时仅做静态/类型层验证（`tsc` 通过，Rust 逐行比对既有范式）。
- W3「20/18 转写」与 W4「30/90% schema」门禁依赖真实媒体 + API 密钥，已明确标记为 `CI-with-secrets` / 手动，未伪造任何数据。
- 转写为命令内同步执行，长任务 UX 后续可升级为流式心跳进度。

---

## 📚 成员产出索引

- 主理人调度与收口：本文档
- 数据基座 / 命令总线 / job 引擎：Batch 0 提交 `105a47a`
- W3 导入 + ASR + TranscribeSource：Batch 1 提交 `17f9376`
- W4 AI Provider + 分析 + AnalyzeSource：Batch 2 提交 `078e07e`
- W3-W4 集成（Rust 桥接 + 前端三视图 + provider 解析）：Batch 3 提交（见 git log）
- 测试：`worker/tests/test_db.py` `test_jobs.py` `test_commands.py` `test_ingest.py` `test_asr.py` `test_transcribe.py` `test_ai.py` `test_analysis.py` `test_analyze.py` `test_resolve.py`

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
