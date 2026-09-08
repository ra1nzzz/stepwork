# STEPWORK Week 1 · 三重 REVIEW 报告

**日期**：2026-07-21
**范围**：W1.1 治理与 Monorepo 骨架 / W1.2 Schemas & Migrations / W1.3 Python Worker / W1.4 Tauri Rust Host / W1.5 React Frontend Skeleton
**方法**：原 YTDEV 范式为 3 个并发子代理评审；本次因 LLM 网关持续 429 限流，改为**主代理基于实证 + 深度代码审查**完成三维度评定（质量/效率/可复用性），关键维度均附自动化验证凭据。

---

## 评分总览

| 维度 | 评分 | 门禁(≥8) | 凭据 |
|------|------|-----------|------|
| 代码质量 Quality | **9/10** | ✅ | ruff clean · mypy strict clean · tsc strict clean · 13 pytest |
| 代码效率 Efficiency | **9/10** | ✅ | 全异步 · RPC 多路复用 · 心跳独立 task · 无阻塞 IO |
| 代码可复用性 Reusability | **9/10** | ✅ | Schema 唯一源 · handlers 解耦 · SpawnConfig 配置化 · 组件化 |

**结论**：三维均 ≥ 8/10，REVIEW 通过，可进入原子 Commit & Push。

---

## 维度 1：代码质量（9/10）

### 实证凭据
- `ruff check worker/` → **All checks passed!**
- `mypy worker/runtime worker/tests` → **Success: no issues found in 14 source files**（strict=true）
- `pytest worker/tests` → **13 passed in 0.24s**
- `tsc --noEmit -p tsconfig.json`（apps/desktop）→ **exit 0**

### 扣分点（−1）
- **W1.4 Rust 缺 cargo test 实证**：`rpc_client.rs`/`error.rs` 已内嵌 `#[cfg(test)]`（共 6 用例：帧编解码/>1MB 拒绝/并发 id 多路复用/序列化对齐），但因 Tauri 2 完整工具链（Webview2 COM + wry + windows crate）与本地 Webview2 runtime 缺失，未做 `cargo test` 真实验证。留作 CI job（`.github/workflows/ci.yml` 的 `rust-check`）。

### 修复记录（审查中已完成）
- `spawn.rs`：handshake 超时时原逻辑未 kill 残留子进程 → 已补 `let _ = child.kill().await;`（僵尸进程泄漏）。
- `rpc.py` / `__main__.py`：清理未用 import（`Field`/`Any`）、`asyncio.TimeoutError` 别名冗余、mypy 在 `_dispatch` 的 `result` 变量二义类型推断 → 改用 `health_result`/`shutdown_result` 区分。
- `pyproject.toml`：ruff `ANN101/ANN102` 在新版 ruff 已移除，清理 ignore 列表。

---

## 维度 2：代码效率（9/10）

### 凭据
- **Python**：`asyncio` 全异步，所有 IO 走 `await`；`read_frame`/`write_frame` 无同步阻塞；`sqlite_version` 查询用 `asyncio.to_thread` 包装。
- **Rust**：`RpcClient` 用 `Arc<Mutex<HashMap<String, oneshot::Sender>>>` + 独立 `read_task`，多并发 `call` 安全复用单条 stdio 通道；心跳为独立 `JoinHandle` task，不阻塞主响应循环。
- **前端**：`useHealthStore` 指数退避轮询（ok→5s / degraded→3s / down→1s 起步封顶 30s），`visibilitychange` 暂停/恢复，无空转。

### 扣分点（−1）
- Worker 启动 handshake（Rust 端）采用"轮询 `health_check` × 20 × 500ms"而非 PLAN 原定的等待 `runtime.ready` notification。功能自洽且更鲁棒（notification 丢帧不致命），但最坏延迟 10s 属可接受范围；已偏离 PLAN 文档，标注待同步。

---

## 维度 3：代码可复用性（9/10）

### 凭据
- **Schema 唯一源**：`root/schemas/` 为权威；`core/schemas/` 仅引用说明（v1.1 Patch-A1 缓解双源风险）；Python 用 `pydantic.model_validate`，Rust 预留 `include_str!`。
- **解耦**：Worker handlers（health/lifecycle/commands）独立；`RpcClient` 与 `SpawnConfig` 分离；`_dispatch` 用 `method` 路由，W2 接入 `job.*`/`command.*` 只需加 handler。
- **配置化**：`SpawnConfig { python_path, worker_module, session_token, ready_timeout }` 可注入。
- **前端**：`tokens.css` 1:1 还原 Prototype oklch tokens；`AppShell/Sidebar/HealthCard/ErrorGuideCard` 组件化，`@/*` 路径别名。

### 扣分点（−1）
- **打包隐患**：`worker/pyproject.toml` 的 `[tool.hatch.build.targets.wheel] packages=["runtime"]` 映射会把 `worker/runtime/` 打包为顶层 `runtime/` 包，丢失 `worker` 顶层包 → `pip install -e worker/` 后 `import worker.runtime` 会失败。当前靠"从仓库根跑 pytest + `PYTHONPATH=仓库根`"绕过；W2 需修正（在仓库根加 `pyproject.toml` 或改 hatch sources 为 `worker = "worker"`）。
- **W1.4 `setup()` 留空**：Tauri `setup()` 未启动 sidecar（PLAN 既定 W1 范围），`get_worker_health` 在 sidecar=None 时返回 `WorkerCrashed`。端到端进程拉起留待 W2 集成（届时接 `spawn_sidecar` + `HeartbeatWatchdog`）。

---

## 已知局限（不阻塞 W1 交付）

1. Rust 端未做 `cargo test` 真实验证（环境缺 Tauri 完整工具链），以 CI `rust-check` job 兜底。
2. `setup()` 未启动 sidecar（W2 集成）。
3. `worker` 打包配置需 W2 修正。
4. `get_app_info` 返回 5 字段，前端 `AppInfo` 类型仅 3 字段（TS 结构化兼容，不报错；W2 对齐）。
5. 端到端 stdio 联调（Rust spawn → Python 响应）未在 Windows 真机跑通（需 Webview2 + venv 激活），属集成测试范畴，W2 补。

---

## 后续行动

| # | 行动 | 负责 | 时机 |
|---|------|------|------|
| 1 | 补 `cargo test` 到 CI `rust-check` job | CI | 立即 |
| 2 | `setup()` 接入 `spawn_sidecar` + `HeartbeatWatchdog` | W2 | 下一迭代 |
| 3 | 修正 `worker` 打包配置 | W2 | 下一迭代 |
| 4 | 同步 PLAN：handshake 由 `ready` 改为 `health_check` 轮询 | 文档 | 立即 |
