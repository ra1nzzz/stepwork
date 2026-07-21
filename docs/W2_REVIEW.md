# W2 交付评审（Triple REVIEW Gate）

**日期**：2026-07-21
**里程碑**：STEPWORK W2 — Sidecar 自愈监控 + 遗留项收口
**范式**：YTDEV（7 阶段原子并发开发）
**评审门禁**：质量 / 效率 / 复用性 三项均 ≥ 8/10 方可合并

---

## 1. W2 目标与交付物

W2 在 W1 三大基座（Monorepo 骨架、Tauri+React 桌面壳、Python Worker Sidecar）之上，补齐 **sidecar 运行时自愈监控** 能力，并收口 W1→W2 的 5 项遗留：

| # | 遗留项 | 状态 | 交付 commit |
|---|---|---|---|
| 2 | `setup()` 接入 `spawn_sidecar` + `HeartbeatWatchdog`，实现自愈重启 | ✅ | `ad7dabe` |
| 3 | worker 打包配置修复 + 测试套件转绿 | ✅ | `9abec8b` |
| 5 | `get_app_info` 类型对齐（5 字段）+ Footer 渲染 | ✅ | `81230d3` |
| 4 | PLAN 握手模型偏差同步（health_check 轮询为权威） | ✅ | `c8fdfa5` |
| 1 | CI 真实关卡启用（cargo / tsc / pytest） | ✅ | `3e4fe5e` |

---

## 2. Triple REVIEW 评分

### 2.1 质量（Quality）— **9 / 10**

- **正确性**：Rust 监控循环正确克隆 `AppState` 内 `Arc` 字段后再跨 `.await`，规避 `E0597` 借用逃逸；重启信号通过 `mpsc::unbounded_channel` 解耦 watchdog 与主循环。
- **通知路由修复（关键）**：`rpc_client::read_loop` 原实现**静默丢弃所有 notification 帧** → 心跳看门狗永远收不到 beat → 自愈循环会无限重启。W2 将 notification 路由到 `notify_handler`，并暴露共享 `Arc<Mutex<Option<Instant>>>` 供 handler 落盘 beat。这是把 #2 从"接线"升级为"真实集成"的根因修复。
- **测试纵深**：Rust 7/7 测试（帧往返、畸形 JSON、超长帧拒绝、并发 ID 唯一、通知路由）；Python 13/13（health/lifecycle/rpc）。新增 `routes_notifications_to_handler` 用 `duplex(4096)` 双通道验证通知转发。
- **类型安全**：`AppInfo` 对齐 Rust `AppState`（5 字段）；mypy `--strict` 通过（15 文件 0 issue）；TS `tsc --noEmit` 双 config 0 error。
- **扣分项（-1）**：端到端（真实 stdio 拉起 Python worker）仅以单测 + mock 覆盖，未跑 `tauri dev` 真机联调。

### 2.2 效率（Efficiency）— **8 / 10**

- **无忙等**：监控循环在 `restart_rx.recv().await` 上挂起，心跳看门狗超时后才触发重启，CPU 零空转。
- **退避友好**：前端健康轮询按 `ok→5s / degraded→3s / down→1s` 指数退避、30s 封顶；文档/可见性暂停逻辑完善。
- **构建隔离**：CI 仅 `cargo check/test`（不跑 `tauri build`），绕开 WebView2 运行时依赖，windows-latest 直接通过。
- **扣分项（-2）**：`pip install -e .[dev]` 在 CI 每次全量重装；`cargo` 缓存键依赖 `Cargo.lock`，但 workspace 尚未固化 lock（见 follow-up）。

### 2.3 复用性（Reusability）— **9 / 10**

- **可测 RpcClient**：`RpcClient::new` 接收 `impl AsyncRead + Unpin + Send` / `impl AsyncWrite + Unpin + Send`，取代写死的 `ChildStdin/ChildStdout`，使通知路由测试无需真实子进程。
- **配置驱动 `SpawnConfig`**：`#[derive(Clone)]` + `cwd` 字段（默认向上回溯至仓库根），`.venv` 优先于系统 `python`，本地与生产行为一致。
- **关注点分离**：`state / spawn / rpc_client / heartbeat / commands` 五模块边界清晰；`HeartbeatWatchdog::with_default_timeout` 返回 `(Self, Arc<...>)` 让调用方持有共享心跳句柄。
- **前端一致性**：`useAppInfoStore` 与既有 `useHealthStore` 同构，Footer 复用 design tokens，零新设计债。

---

## 3. 验证矩阵（全绿）

| 关卡 | 命令 | 结果 |
|---|---|---|
| Rust check | `cargo check --all-targets` | 0 错误 0 警告 |
| Rust clippy | `cargo clippy --all-targets -- -D warnings` | 干净 |
| Rust fmt | `cargo fmt --all -- --check` | 一致 |
| Rust test | `cargo test --all-targets` | **7 / 7 通过** |
| Python ruff | `ruff check worker/` | All checks passed |
| Python mypy | `mypy worker/` | Success, no issues (15 files) |
| Python pytest | `pytest worker/tests` | **13 / 13 通过** |
| TS typecheck | `tsc --noEmit -p tsconfig.json` | exit 0 |
| TS typecheck (node) | `tsc --noEmit -p tsconfig.node.json` | exit 0 |
| CI 配置 | `pyyaml.safe_load` | YAML OK，3 jobs 解析正常 |

---

## 4. 关键决策与根因

- **握手模型偏差（W2 #4）**：W1 PLAN 把 `runtime.ready` notification 当成阻塞式握手门（host 等 10s `ready_timeout`）。实际改为 **`runtime.health_check` 请求-响应轮询为权威握手**：避免单条 notification 丢失导致启动挂死，且与心跳看门狗解耦。PLAN 已同步。
- **打包根因（W2 #3）**：原 `worker/pyproject.toml` 把 wheel 映射为顶层 `runtime` 包，导致 `import worker.runtime` 失败。改为仓库根 `pyproject.toml` + `packages=["worker"]`，`pip install -e .` 产出可导入的 `worker.runtime`（已验证 `import OK, version=0.1.0`）。
- **pytest-asyncio 配置 typo（W2 #3）**：`[tool.pytest.ini-options]`（连字符）使 pytest 完全忽略该段 → 插件不读 `asyncio_mode=auto` → 所有 async 测试报 "async def functions are not natively supported"。修正为 `[tool.pytest.ini_options]`（下划线）+ `asyncio_mode` / `asyncio_default_fixture_loop_scope` 下划线键。
- **mypy 收窄误判（W2 #3）**：`test_lifecycle.py` 中 `assert worker_state.last_heartbeat_at is None` 把属性收窄为字面 `None`，mypy 因看不到 `touch_heartbeat` 副作用而误判后续断言不可达。改为先捕获初值到局部变量再断言，打破收窄。

---

## 5. Follow-up / 已知局限

1. **真机联调**：需在 `tauri dev` 下验证 Rust ↔ Python 真实 stdio 握手与自愈重启（当前以单测 + mock 覆盖）。
2. **Cargo.lock 固化**：建议提交 `apps/desktop/src-tauri/Cargo.lock` 以稳定 CI 缓存键与可重现构建。
3. **覆盖率上报**：CI 已留 `pytest-cov` 依赖，可后续加 `--cov=worker --cov-report=xml` + codecov 步骤。
4. **重启风暴护栏**：当前崩溃后固定 `sleep(500ms)` 即重启，未做"连续 N 次快速崩溃则放弃"的熔断，建议 W3 补 `CircuitBreaker`。

---

## 6. 评审结论

| 维度 | 评分 | 门禁 |
|---|---|---|
| 质量 Quality | 9/10 | ✅ ≥ 8 |
| 效率 Efficiency | 8/10 | ✅ ≥ 8 |
| 复用性 Reusability | 9/10 | ✅ ≥ 8 |

**Verdict：🔴→🟢 PASS。** 三项均达门禁，W2 全部遗留收口，可合并并推送 `origin/main`。
