# STEPWORK W3-W4 执行计划（YTDEV 原子并发）

**版本：v1.1（含 3 角色头脑风暴补丁）**
**日期：2026-07-21**
**上位文档**：STRATEGY_PLAN.md §3.3（W3/W4）、SYSTEM_SPEC §7-§10、migrations/*、schemas/*
**范式**：YTDEV（7 阶段：调研→PLAN→头脑风暴→终稿→并发实现→三重 REVIEW→原子提交）

---

## 0. 范围现实（Stage 1 调研结论）

| 已具备（W1 产物） | 缺失（本计划须建） |
|---|---|
| `migrations/0001-0003.sql`（5 张核心表 + 审计 + agent 占位） | Python DB 访问层（migrate runner + repositories） |
| `schemas/command-envelope|artifact-envelope|error-envelope|job-*.enum.json` | 领域模型（pydantic）、Job 引擎（lease/heartbeat/重试） |
| Worker RPC 骨架（lib.rs monitor + rpc_client 帧路由 + handlers/commands.py） | Command Bus（envelope 校验 + 分发到 handler） |
| Tauri 桌面壳 + Footer + HealthCard + 模拟 tauri 层 | W3/W4 的领域 handler + Provider + 前端视图 |

**结论**：W3/W4 功能无法脱离数据底座独立运行。本计划将"数据底座运行时"作为 **Batch 0** 前置构建（对应 STRATEGY 的 W2，此前被 sidecar 硬化替代），再并发推进 W3、W4。

---

## 1. 模块编号体系（全局唯一前缀）

```
L.1  DB 层        worker/runtime/db/{connection,migrations,repos}.py
L.2  领域模型      worker/runtime/models.py
L.3  Job 引擎      worker/runtime/jobs/{engine,lease}.py
L.4  Command Bus   worker/runtime/commands/{bus,envelope}.py
L.5  W3 Ingest     worker/runtime/ingest/{hash,metadata}.py
L.6  W3 ASR        worker/runtime/providers/asr/{base,local,cloud}.py
L.7  W3 处理       worker/runtime/commands/handlers/{import_source,transcribe_source}.py
L.8  W4 AI         worker/runtime/providers/ai/{base,cloud,openai_compatible}.py
L.9  W4 分析       worker/runtime/analysis/{prompt,schema,report}.py
L.10 W4 处理       worker/runtime/commands/handlers/analyze_source.py
L.11 引导/集成     worker/runtime/bootstrap.py（启动 migrate）+ Rust 桥接命令
L.12 前端 W3       apps/desktop/src/{features/import,features/transcript}/...
L.13 前端 W4       apps/desktop/src/features/analysis/...
```

---

## 2. 接口签名（核心契约）

```python
# L.1 DB
def connect(db_path: Path) -> Connection          # WAL, foreign_keys=ON
def run_migrations(conn, migrations_dir) -> int    # 幂等，返回已应用版本
class Repos: source_assets, jobs, projects, workspaces, content_versions

# L.2 Models (pydantic v2, frozen-ish)
SourceAsset(id, project_id, kind, local_uri, content_hash, metadata, ...)
Job(id, job_type, state, stage, payload, lease_owner, lease_expires_at, ...)
Workspace / ContentProject / ContentVersion / ArtifactEnvelope

# L.3 Job Engine
create_job(conn, job_type, payload, max_attempts=3) -> Job
acquire_lease(conn, job_id, owner, ttl_sec) -> bool
record_heartbeat(conn, job_id) -> None
transition(conn, job_id, to_state, progress=None, error=None) -> Job
sweep_expired_leases(conn, now) -> list[Job]        # kill -9 恢复核心
retry_eligible(conn, now) -> list[Job]

# L.4 Command Bus
parse_envelope(raw: dict) -> CommandEnvelope       # 校验 command-envelope.schema.json
dispatch(env, deps) -> CommandResult               # 路由 import_source/transcribe_source/analyze_source

# L.6 ASR Provider
class ASRProvider(Protocol):
    name: str
    async def transcribe(self, media_uri, opts) -> Transcript   # segments[{start,end,text}]

# L.8 AI Provider
class AIProvider(Protocol):
    name: str; model: str; estimated_cost_per_1k: float
    async def complete(self, prompt, schema) -> dict

# L.9 Analysis
build_analysis_prompt(source_meta, brand) -> str
AnalysisReport = pydantic model (validate against analysis.schema.json)
```

---

## 3. Batch 编排（无交叉、可并发）

```
Batch 0  (顺序, 主代理手写)  L.1+L.2+L.3+L.4+L.11(bootstrap)
        → pytest: migrate 幂等、lease 获取/过期 sweep、envelope 校验
Batch 1  (并发子代理)        W3 = L.5 + L.6 + L.7
        A: ingest(hash/metadata) + import_source handler
        B: asr providers(local 确定性实现 + cloud 骨架+env 接线)
        C: 前端 import + transcript 视图
Batch 2  (并发子代理)        W4 = L.8 + L.9 + L.10
        D: ai providers(cloud + openai_compatible/ollama)
        E: analysis(prompt+schema+report) + analyze_source handler
        F: 前端 analysis 视图
Batch 3  (主代理集成)        L.11 Rust 桥接(tauri command dispatch_command → worker RPC)
                              + types.ts 领域类型扩展 + tauri.ts mock 同步
```

**共享文件**（主代理统一改，禁止子代理交叉）：
- `apps/desktop/src/lib/types.ts`（集中扩展领域类型）
- `apps/desktop/src/lib/tauri.ts`（mock 同步新命令）
- `worker/runtime/handlers/commands.py`（注册新 RPC 方法 → 调 Command Bus）
- `docs/W3_W4_REVIEW.md`（三重 REVIEW 汇总）

---

## 4. 三角色头脑风暴补丁（P0/P1）

| 角色 | 发现 | 级别 | 合入 |
|---|---|---|---|
| 架构师 | Job 引擎与 Command Bus 必须共用同一 `conn`/事务边界，否则 lease 与 payload 写入不一致 | P0 | L.3/L.4 共用 `Repos` 注入 |
| 安全健壮性 | ASR/AI 的 cloud provider 必须**零密钥硬编码**；密钥仅来自 env / Tauri 安全存储；transcript 落库前做大小与字符上限保护 | P0 | L.6/L.8 + L.5 限流 |
| 安全健壮性 | 导入文件 hash 用 sha256 防碰撞；metadata 解析失败不得崩溃（降级为 `{}`） | P1 | L.5 |
| UX | 长任务（转写/分析）UI 必须展示：阶段、进度、取消、失败原因（PRD §338） | P0 | L.12/L.13 |
| UX | 失败可重试；Provider 切换入口（W4 gate 要求失败可切换） | P1 | L.13 |
| 架构师 | 离线环境无真实 API：cloud provider 用 `httpx`+env 接线但**单测走 mock**；local ASR 用确定性 fixture 满足 ≥18/20 的"可运行"证伪 | P1 | L.6/L.8 |

---

## 5. 验证矩阵（质量门禁）

| 层 | 命令 | 通过线 |
|---|---|---|
| Python | `pytest worker/tests -q` | 全绿（含 migrate 幂等 / lease sweep / envelope / ingest hash / asr local / analysis schema） |
| Python | `mypy worker/`（strict, 同 W2 配置） | 0 issue |
| Python | `ruff check worker/` | clean |
| 前端 | `tsc --noEmit -p tsconfig.json` + `tsconfig.node.json` | exit 0 |
| Rust | `cargo test --all-targets`（仅 dispatch_command 单测，不跑 tauri build） | 7/7 → +新增 |

**STRATEGY Gate 说明**：
- W3 "20 视频 ≥18 转写"、W4 "30 样本 ≥90% schema 合法" 需真实媒体 + API 密钥 → 标记为 **CI-with-secrets / 手动 gate**，本交付提供 machinery + fixture 单测 + 透明接线，不伪造真实通过率。

---

## 6. 风险与遗留

- **429 限流**：并发子代理可能被打断 → Batch 1/2 若子代理失败，主代理手写补齐（沿用 W1 实战注记）。
- **WebView2 缺失**：Rust 仅跑 `cargo test`（单测），不 `tauri build`。
- **遗留（下个增量）**：真实 media 转写/分析 E2E、Tauri 安全存储接线密钥、Provider 切换 UI 完善、kill -9 恢复每晚自动测（W2 注记已提 CircuitBreaker）。
