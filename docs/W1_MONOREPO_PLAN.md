# STEPWORK Week 1 · Monorepo 与桌面骨架 YTDEV PLAN

**版本：v1.1**（已合入头脑风暴全部 P0 修复）
**日期：2026-07-21**
**范式：YTDEV（调研→PLAN→头脑风暴→最终PLAN→子代理并发原子实现→三重并行REVIEW→原子级COMMIT&PUSH）**
**上位文档：STRATEGY_PLAN.md、MVP_PLAN.md、SYSTEM_SPEC.md**
**GitHub：https://github.com/ra1nzzz/stepwork**

---

## 1. 目标（Week 1 Gate）

按 `MVP_PLAN.md Week 1` 与 `STRATEGY_PLAN.md §3.3 W1`：

- ✅ Monorepo 目录结构落地（SYSTEM_SPEC §5）
- ✅ Tauri + React + Python Sidecar 可启动/健康检查/退出
- ✅ CI 占位（Rust/TS/Python）
- ✅ 冻结目录结构：`STEPWORK_HOME/workspaces/<workspace-id>/projects/<project-id>/`
- ✅ 冻结 Schema：Command Envelope v1、Artifact Envelope v1
- ✅ `media-auto-pilot` 审计占位文档
- ✅ 许可证文件（AGPL-3.0 Core + Apache-2.0 SDK）
- ✅ ADR-001—010 占位
- ✅ Windows 可构建（构建脚本 + 配置）

**Gate**：Tauri 能启动 Sidecar、获得健康状态并正常退出。

---

## 2. 模块编号体系

Week 1 共拆 **5 个原子模块**，全局唯一前缀 `W1.<n>`：

| 模块编号 | 名称 | 职责 | Batch |
|---|---|---|---|
| **W1.1** | Monorepo Skeleton & Governance | 目录树、LICENSE、治理文档、CI 占位 | A |
| **W1.2** | Schemas & Migrations | Command/Artifact Envelope JSON Schema、SQLite 初始迁移 | A |
| **W1.3** | Python Worker Sidecar | stdio JSON-RPC、runtime.ready、heartbeat、health_check | B |
| **W1.4** | Tauri Rust Host | Tauri 2 主进程、Sidecar 启停、Capabilities、配置 | B |
| **W1.5** | React Frontend Skeleton | Vite+React+TS 启动页、Health 状态展示、Tokens 落位 | C |

**原子模块原则**：每个模块独立实现、独立测试、独立提交。文件路径无交叉，Batch 内子代理可无干扰并行。

---

## 3. 模块详设

### W1.1 · Monorepo Skeleton & Governance

**职责**：建立 SYSTEM_SPEC §5 定义的完整目录树 + 合规/治理文档基线。

**文件清单**（全部为新建）：
```
stepwork/
├── apps/desktop/{src,src-tauri}/.gitkeep
├── core/{domain,application,commands,events,policies,schemas}/.gitkeep
├── worker/{runtime,tasks,providers,media}/.gitkeep
├── agent-interop/{cli,mcp,a2a,acp,adapters}/.gitkeep
├── publisher-engine/{browser,dom,uploader,runtime,rpc}/.gitkeep
├── sdk/{python,typescript,plugin,agent-adapter}/.gitkeep
├── plugins/{official,examples,registry}/.gitkeep
├── schemas/.gitkeep
├── migrations/.gitkeep
├── tests/.gitkeep
├── scripts/.gitkeep
├── LICENSE-AGPL-3.0.txt          # Core
├── LICENSE-APACHE-2.0.txt         # SDK
├── LICENSE                        # 指向双许可证说明
├── THIRD_PARTY_NOTICES.md         # 占位
├── SECURITY.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── GOVERNANCE.md
├── TRADEMARK.md
├── ROADMAP.md                     # 引用 PHASE_PLAN
├── docs/DECISIONS.md              # 固化 STRATEGY_PLAN §1.2 的 4 项决策
├── docs/MIGRATION_ASSESSMENT.md   # media-auto-pilot 审计占位
├── docs/LICENSE_AUDIT.md          # 占位
├── docs/adr/ADR-001-tauri-react.md
├── docs/adr/ADR-002-python-sidecar.md
├── docs/adr/ADR-003-sqlite-wal.md
├── docs/adr/ADR-004-command-bus.md
├── docs/adr/ADR-005-artifact-first.md
├── docs/adr/ADR-006-agpl-apache.md
├── docs/adr/ADR-007-cli-mcp-first.md
├── docs/adr/ADR-008-publisher-fill-preview.md
├── docs/adr/ADR-009-plugin-isolated-process.md
├── docs/adr/ADR-010-media-auto-pilot-migration.md
└── .github/workflows/ci.yml       # Rust+TS+Python 占位
```

**接口/契约**：
- 所有目录创建后即提交（.gitkeep 保留空目录）
- LICENSE 是双许可证入口说明文件，明确 Core=AGPL-3.0-or-later、SDK=Apache-2.0
- DECISIONS.md 引用 STRATEGY_PLAN §1.2 的 4 项决策原文
- ADR 模板：Status/Context/Decision/Consequences 四段

**测试要点**：
- 目录树结构与 SYSTEM_SPEC §5 一致（脚本校验）
- LICENSE 引用正确
- CI 占位文件语法正确（`yamllint` 或 GitHub Actions 在线校验）

---

### W1.2 · Schemas & Migrations

**职责**：冻结 Command Envelope v1 与 Artifact Envelope v1，落地 SQLite 初始迁移。

**文件清单**：
```
schemas/
├── command-envelope.schema.json      # SYSTEM_SPEC §7.1
├── artifact-envelope.schema.json     # SYSTEM_SPEC §11.1
├── job-state.enum.json               # SYSTEM_SPEC §10.1 主状态 9 个
├── job-stage.enum.json               # SYSTEM_SPEC §10.1 业务阶段 9 个
├── error-envelope.schema.json        # SYSTEM_SPEC §16
└── README.md                          # Schema 使用说明

migrations/
├── 0001_init.sql                      # 5 张初始表
├── 0002_audit_events.sql              # 审计表
└── README.md                          # 迁移规则

core/schemas/                          # 软链或复制（供 Rust/Python 引用）
└── README.md                          # 说明引用根 schemas/
```

**0001_init.sql 表结构**（依据 SYSTEM_SPEC §8 + 调研子代理 B §2）：

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE workspaces (
  id TEXT PRIMARY KEY,                    -- uuid
  name TEXT NOT NULL UNIQUE,
  root_path TEXT NOT NULL,
  settings TEXT NOT NULL DEFAULT '{}',     -- JSON
  created_at TEXT NOT NULL,                -- ISO-8601
  archived_at TEXT
);

CREATE TABLE content_projects (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  brand_profile_id TEXT,
  current_content_version_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_content_projects_workspace ON content_projects(workspace_id);

CREATE TABLE source_assets (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES content_projects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                      -- video/audio/image/text/link
  local_uri TEXT NOT NULL,
  original_uri TEXT,
  content_hash TEXT NOT NULL,              -- SHA-256
  rights_declaration TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE (project_id, content_hash)
);
CREATE INDEX idx_source_assets_project ON source_assets(project_id);
CREATE INDEX idx_source_assets_hash ON source_assets(content_hash);

CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  job_type TEXT NOT NULL,                  -- transcribe/analyze/render/publish/...
  state TEXT NOT NULL,                     -- 见 job-state.enum.json
  stage TEXT,                              -- 见 job-stage.enum.json
  payload TEXT NOT NULL DEFAULT '{}',
  progress REAL NOT NULL DEFAULT 0.0,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  lease_owner TEXT,
  lease_expires_at TEXT,
  heartbeat_at TEXT,
  error_code TEXT,
  result_artifact_ids TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_jobs_state_lease ON jobs(state, lease_expires_at);
CREATE INDEX idx_jobs_type ON jobs(job_type);

CREATE TABLE content_versions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES content_projects(id) ON DELETE CASCADE,
  parent_version_id TEXT REFERENCES content_versions(id) ON DELETE SET NULL,
  content_type TEXT NOT NULL,              -- script/analysis/topic/...
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  producer TEXT NOT NULL DEFAULT '{}',     -- JSON: {type,id,protocol}
  created_at TEXT NOT NULL
);
CREATE INDEX idx_content_versions_project ON content_versions(project_id);
```

**0002_audit_events.sql**：
```sql
CREATE TABLE audit_events (
  id TEXT PRIMARY KEY,
  actor TEXT NOT NULL,                     -- JSON
  source_protocol TEXT NOT NULL,           -- ui/cli/mcp/a2a/acp/plugin/scheduled
  command TEXT NOT NULL,
  target TEXT,
  requested_scope TEXT,
  approval TEXT,
  result TEXT,
  correlation_id TEXT,
  timestamp TEXT NOT NULL
);
CREATE INDEX idx_audit_correlation ON audit_events(correlation_id);
CREATE INDEX idx_audit_timestamp ON audit_events(timestamp);
```

**接口/契约**：
- 所有 Schema 文件遵循 JSON Schema Draft 2020-12
- `command-envelope.schema.json` 字段名/必填项与 SYSTEM_SPEC §7.1 完全一致
- `artifact-envelope.schema.json` 字段与 SYSTEM_SPEC §11.1 完全一致
- 迁移文件命名 `NNNN_description.sql`，严格递增
- 每个迁移文件头部注释：版本号、上游版本、说明

**测试要点**：
- 用 `jsonschema` Python 包或 `ajv` Node 包对示例 Command/Artifact 校验通过
- 用 `sqlite3 :memory:` 执行 `0001_init.sql` + `0002_audit_events.sql` 不报错
- 状态枚举值与 SYSTEM_SPEC §10.1 完全一致

---

### W1.3 · Python Worker Sidecar

**职责**：实现可被 Tauri Sidecar 启动的最小 Python Worker，支持 stdio JSON-RPC + 心跳 + 健康检查。

**文件清单**：
```
worker/
├── pyproject.toml                       # 依赖：pydantic>=2.7, sqlalchemy>=2.0, typer>=0.12
├── README.md
└── runtime/
    ├── __init__.py
    ├── __main__.py                      # python -m worker.runtime 入口
    ├── rpc.py                           # JSON-RPC over stdio（长度前缀帧）
    ├── heartbeat.py                     # 5 秒心跳发送 runtime.heartbeat
    ├── handlers/
    │   ├── __init__.py
    │   ├── health.py                    # runtime.health_check
    │   └── lifecycle.py                 # runtime.ready / runtime.shutdown
    └── state.py                         # WorkerState（运行中标记、启动时间）
```

**接口签名**（冻结）：

```python
# worker/runtime/rpc.py
class RpcFrame(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    method: str | None = None
    params: dict[str, Any] | None = None
    result: Any | None = None
    error: RpcError | None = None

class RpcError(BaseModel):
    code: int
    message: str
    data: dict[str, Any] | None = None

async def read_frame(reader: asyncio.StreamReader) -> RpcFrame: ...
async def write_frame(writer: asyncio.StreamWriter, frame: RpcFrame) -> None: ...
```

```python
# worker/runtime/handlers/health.py
class HealthStatus(BaseModel):
    status: Literal["ok", "degraded", "down"]
    version: str
    uptime_seconds: float
    python_version: str
    sqlite_version: str
    active_jobs: int = 0
    degraded_reasons: list[str] = Field(default_factory=list)

async def handle_health_check(params: dict[str, Any] | None) -> HealthStatus: ...
```

**JSON-RPC 方法清单（W1 最小集）**：
| Method | Params | Result | 说明 |
|---|---|---|---|
| `runtime.ready` | — | `{ready: true, pid, started_at}` | Sidecar 启动后立即发送（notification） |
| `runtime.heartbeat` | — | `{alive: true, timestamp}` | 每 5 秒（notification） |
| `runtime.health_check` | `{}` | `HealthStatus` | 请求-响应 |
| `runtime.shutdown` | `{graceful: bool}` | `{bye: true}` | 请求-响应，触发优雅退出 |

**帧格式**（长度前缀）：
```
[4 字节大端 uint32 长度][JSON UTF-8 字节流]
```

**测试要点**（`worker/tests/`）：
- `test_rpc.py`：read_frame/write_frame 编解码对称、错误帧处理、长度前缀边界（>1MB 拒绝）
- `test_health.py`：HealthStatus schema 完整、字段类型正确
- `test_lifecycle.py`：模拟启动→ready→heartbeat→health_check→shutdown 全流程
- 覆盖率 ≥ 80%

**依赖**（`worker/pyproject.toml`）：
```toml
[project]
name = "stepwork-worker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "pydantic>=2.7",
  "sqlalchemy>=2.0",
  "typer>=0.12",
]
[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "pytest-cov>=5", "ruff>=0.5"]
```

---

### W1.4 · Tauri Rust Host

**职责**：实现 Tauri 2 主进程，负责 Sidecar 启停、心跳接收、健康检查转发、Capabilities。

**文件清单**：
```
apps/desktop/src-tauri/
├── Cargo.toml                           # tauri 2, tauri-plugin-shell
├── build.rs
├── tauri.conf.json                      # 窗口、bundle、安全
├── capabilities/
│   └── default.json                     # 最小权限
├── icons/                               # 占位（README 说明）
│   └── README.md
└── src/
    ├── main.rs                          # 入口
    ├── lib.rs                           # run() 函数
    ├── sidecar/
    │   ├── mod.rs
    │   ├── spawn.rs                     # 启动 Python sidecar
    │   ├── rpc_client.rs                # 长度前缀帧读写
    │   └── heartbeat.rs                 # 心跳接收任务
    ├── commands/
    │   ├── mod.rs
    │   └── health.rs                    # get_worker_health Tauri Command
    └── state.rs                         # AppState（SidecarHandle）
```

**Cargo.toml 依赖**：
```toml
[package]
name = "stepwork-desktop"
version = "0.1.0"
edition = "2021"

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "2", features = [] }
tauri-plugin-shell = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tokio = { version = "1", features = ["full"] }
anyhow = "1"
thiserror = "1"
tracing = "0.1"
tracing-subscriber = "0.3"
uuid = { version = "1", features = ["v4", "serde"] }
```

**核心接口**：
```rust
// sidecar/rpc_client.rs
pub struct RpcClient { /* tokio Child stdin/stdout */ }
impl RpcClient {
    pub async fn call(&mut self, method: &str, params: serde_json::Value)
        -> Result<serde_json::Value, SidecarError>;
    pub async fn notify(&mut self, method: &str, params: serde_json::Value)
        -> Result<(), SidecarError>;
}

// commands/health.rs
#[tauri::command]
pub async fn get_worker_health(state: tauri::State<'_, AppState>)
    -> Result<HealthStatus, String>;
```

**Tauri Commands 暴露给前端**：
| Command | 参数 | 返回 | 说明 |
|---|---|---|---|
| `get_worker_health` | — | `HealthStatus` | 转发到 sidecar `runtime.health_check` |
| `restart_worker` | — | `()` | 重启 sidecar |
| `get_app_info` | — | `{version, platform, stepwork_home}` | 应用信息 |

**tauri.conf.json 关键配置**：
```json
{
  "productName": "STEPWORK",
  "version": "0.1.0",
  "identifier": "com.stepwork.app",
  "build": {
    "frontendDist": "../dist",
    "devUrl": "http://localhost:1420",
    "beforeDevCommand": "npm run dev",
    "beforeBuildCommand": "npm run build"
  },
  "app": {
    "windows": [{
      "title": "STEPWORK",
      "width": 1440, "height": 900,
      "minWidth": 1120, "minHeight": 700,
      "decorations": true, "resizable": true
    }],
    "security": { "csp": "default-src 'self'; style-src 'self' 'unsafe-inline'" }
  },
  "bundle": {
    "active": true,
    "targets": ["nsis"],
    "icon": ["icons/icon.ico"]
  }
}
```

**测试要点**：
- `cargo check` 通过
- `cargo clippy -- -D warnings` 无错
- `cargo test`（至少 sidecar 帧编解码、错误处理 4 个测试）
- 手动验证：`cargo tauri dev` 能启动（前置：Python sidecar 已就绪）

---

### W1.5 · React Frontend Skeleton

**职责**：Vite+React+TS 启动页，落地 design tokens，展示 Sidecar 健康状态。

**文件清单**：
```
apps/desktop/
├── package.json                         # react@18, vite@5, zustand, @tanstack/react-query, @tauri-apps/api
├── tsconfig.json
├── tsconfig.node.json
├── vite.config.ts
├── index.html
├── .npmrc                               # 强制使用 managed Node
└── src/
    ├── main.tsx
    ├── App.tsx                          # 单页：Logo + Health 状态卡 + 版本号
    ├── vite-env.d.ts
    ├── styles/
    │   ├── tokens.css                   # 完整 :root tokens（来自 Prototype）
    │   └── global.css                   # reset + 基础排版
    ├── components/
    │   └── HealthCard.tsx               # 调用 get_worker_health 并渲染
    ├── stores/
    │   └── useHealthStore.ts            # Zustand：health polling（5s）
    └── lib/
        └── tauri.ts                     # @tauri-apps/api 封装
```

**package.json 关键依赖**：
```json
{
  "name": "stepwork-desktop",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "tauri": "tauri"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "@tauri-apps/api": "^2.0.0",
    "zustand": "^4.5.0",
    "@tanstack/react-query": "^5.50.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "@tauri-apps/cli": "^2.0.0",
    "typescript": "^5.5.0",
    "vite": "^5.3.0"
  }
}
```

**tokens.css**（完整复制 Prototype `styles.css` L1-21 的 :root）：
```css
:root {
  --bg: oklch(13% 0.018 252);
  --surface: oklch(18% 0.024 252);
  --surface-2: oklch(22% 0.028 252);
  --fg: oklch(95% 0.01 240);
  --muted: oklch(68% 0.024 245);
  --border: oklch(29% 0.03 250);
  --accent: oklch(78% 0.16 190);
  --accent-2: oklch(68% 0.16 285);
  --success: oklch(74% 0.15 150);
  --warning: oklch(80% 0.15 80);
  --danger: oklch(69% 0.19 25);
  --font-display: "Avenir Next", "SF Pro Display", "Segoe UI", system-ui, sans-serif;
  --font-body: "SF Pro Text", "Segoe UI", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  --radius-sm: 8px;
  --radius: 13px;
  --radius-lg: 20px;
  --sidebar: 232px;
  --topbar: 72px;
}
```

**App.tsx 渲染逻辑**：
- 进入后调用 `useHealthStore.startPolling()`，每 5s 调用 `get_worker_health`
- 状态映射：`ok` → 绿色圆点 + "核心引擎运行正常"；`degraded` → 黄色；`down` → 红色
- 展示字段：version、uptime_seconds、python_version、active_jobs
- 失败时显示错误 + 重试按钮

**测试要点**：
- `npm install` 成功
- `npm run build` 通过
- `npm run dev` 在 1420 端口启动
- `tsc --noEmit` 无错

---

## 4. Batch 编排

| Batch | 模块 | 子代理 | 可并行 | 文件冲突风险 |
|---|---|---|---|---|
| **A** | W1.1 Skeleton + W1.2 Schemas | 1 个 general-purpose | 与 B、C 并行 | 无（独立目录） |
| **B** | W1.3 Worker + W1.4 Tauri Rust | 2 个 general-purpose 并行 | 与 A、C 并行 | 无（worker/ vs apps/desktop/src-tauri/） |
| **C** | W1.5 React Frontend | 1 个 general-purpose | 与 A、B 并行 | 无（apps/desktop/src/） |

**集成顺序**（主代理统一处理）：
1. Batch A 完成 → 目录树/Schemas 就绪
2. Batch B 完成 → Worker 可被独立测试（`python -m worker.runtime`）
3. Batch C 完成 → 前端可独立 dev（`npm run dev`，Tauri mock）
4. **主代理集成**：在 `apps/desktop/src-tauri/tauri.conf.json` 中注册 sidecar 路径 → `cargo tauri dev` 联调
5. 主代理补充：根级 `README.md`（项目入口）、`package.json` workspace（可选）、根级 `rust-toolchain.toml`

**共享文件集成策略**：
- `tauri.conf.json` 由 W1.4 子代理创建基础版，**主代理**在集成阶段补充 `bundle.externalBin` 指向 sidecar 二进制
- 根 `README.md` 由**主代理**在所有 Batch 完成后统一编写
- `.github/workflows/ci.yml` 由 W1.1 子代理创建占位，**主代理**在所有模块就绪后补充完整 job

---

## 5. 验收测试矩阵（Week 1 Gate）

| 场景 | 通过标准 |
|---|---|
| `python -m worker.runtime` 启动 | 5 秒内 stdout 出现 `runtime.ready` notification |
| Worker health_check | 返回 `status=ok`、`python_version=3.13.x`、`sqlite_version>=3.40` |
| Worker 心跳 | 启动后 5 秒/次发送 `runtime.heartbeat` |
| Worker 优雅退出 | 接收 `runtime.shutdown` 后 2 秒内退出码 0 |
| `cargo check` (src-tauri) | 0 错误 0 警告 |
| `cargo clippy` | 无 denied 警告 |
| `cargo test` (src-tauri) | ≥ 4 个测试通过 |
| `npm run build` (apps/desktop) | 成功产出 dist/ |
| `tsc --noEmit` | 0 错误 |
| `cargo tauri dev` 联调 | 窗口打开，显示 health=ok |
| Schema 校验 | 示例 Command/Artifact 通过 ajv/jsonschema |
| `sqlite3 :memory:` 执行迁移 | 不报错，外键生效 |
| CI 占位 | `.github/workflows/ci.yml` 语法正确 |

---

## 6. 风险与回滚

| 风险 | 概率 | 应对 |
|---|---|---|
| Rust 工具链安装失败 | 低 | 重装 / 切换到 Electron 备选（需回写 DECISIONS.md） |
| Tauri Sidecar 在 Windows 打包路径问题 | 中 | 用 `tauri::utils::platform::resource_dir`；失败则降级为开发模式说明 |
| Python 依赖版本冲突 | 低 | 严格 pydantic>=2.7、sqlalchemy>=2.0，使用 venv 隔离 |
| oklch 在旧版 WebView 不支持 | 低 | Tauri 2 使用 WebView2 (Chromium)，原生支持 |
| GitHub push 失败 | 低 | 检查 token scopes；备选：手动在网页建仓库 |

**回滚策略**：每个模块独立 commit，单模块失败可 `git revert <commit>` 不影响其他模块。

---

## 7. 后续 Week 2 预留接口

- `worker/runtime/handlers/` 已存在 `health.py`，Week 2 添加 `commands.py`（Command Bus handler）
- `worker/runtime/state.py` 已抽象 WorkerState，Week 2 扩展 JobLease 字段
- `apps/desktop/src-tauri/src/sidecar/rpc_client.rs` 已是通用 JSON-RPC client，Week 2 添加 `send_command`
- `migrations/0001_init.sql` 已包含 `jobs` 表，Week 2 直接基于该表实现 Job Engine

---

## 8. 决策引用（来自 STRATEGY_PLAN §1.2）

1. 任务状态机以 SYSTEM_SPEC §10.1 为准（9 主状态 + 9 业务阶段）
2. 文件目录采用 `workspaces/<workspace-id>/projects/<project-id>/`
3. 周期估算以 PHASE_PLAN 为准（单人 10-12 个月）
4. Prototype 仅作桌面视觉基线，移动端不在 V0.1 范围

---

## 9. v1.1 头脑风暴修复补丁（P0 全收）

### 9.1 架构师 P0 修复

#### Patch-A1：Schema 单一源
- **决策**：Schema 唯一存放 `schemas/`（根目录），`core/schemas/` 不建独立副本，仅以 `README.md` 说明"引用根 schemas/"。
- **理由**：避免软链/复制双源漂移；Rust/Python 通过构建脚本统一从根 `schemas/` 读取。
- **行动**：W1.1 文件清单保留 `schemas/`，`core/schemas/README.md` 内容改为引用说明。

#### Patch-A2：Command Bus 宿主归属
- **决策**：Command Bus 宿主为 **Python Worker**（与 SYSTEM_SPEC §4 图示"Tauri Rust Host → Application Gateway"的实现层选择解耦；Tauri 仅做 Sidecar 生命周期 + 权限 + 凭据，业务 Command 一律转发到 Worker）。
- **理由**：核心业务逻辑/数据模型/Job Engine 都在 Python 侧（pydantic/SQLAlchemy 生态），Tauri Rust 保持薄壳。这符合"本地业务层 Python 3.12"（SYSTEM_SPEC §3.3）的定位。
- **行动**：在 docs/DECISIONS.md 中记录为第 5 项决策；W1.3 增加 `worker/runtime/handlers/commands.py` 占位；W1.4 增加 `src/commands/mod.rs` 仅做转发。

#### Patch-A3：协议版本协商
- **决策**：`runtime.ready` 的 result 增加 `protocol_version: "1"` 与 `capabilities: ["health","heartbeat","commands","jobs"]`；帧格式不变。
- **行动**：W1.3 `handlers/lifecycle.py` 实现版本协商。

#### Patch-A4：迁移表补全
- **决策**：W1.2 拆分为 `0001_init.sql`（5 张核心表）+ `0002_audit_events.sql` + `0003_agent_placeholder.sql`（agent_connections/agent_capabilities/agent_sessions/agent_tasks/agent_artifacts/approval_requests/platform_variants/publish_jobs/provenance_records，全部为占位空表，仅结构与索引，W2 填充数据）。
- **行动**：W1.2 文件清单追加 `0003_agent_placeholder.sql`。

### 9.2 安全工程师 P0 修复

#### Patch-S1：RPC 并发互斥与请求多路复用
- **决策**：`RpcClient` 内部维护 `HashMap<RequestId, oneshot::Sender>`，独立 read task 按 id 分发；外部用 `Arc<Mutex<RpcClient>>` 共享。
- **行动**：W1.4 `sidecar/rpc_client.rs` 实现请求-响应配对。

#### Patch-S2：Sidecar 启动失败感知
- **决策**：Tauri 启动 sidecar 后启动 10s `ready_timeout`；stderr 捕获；启动失败定义 `SidecarErrorKind::{PythonMissing, SpawnFailed, HandshakeTimeout}`。
- **行动**：W1.4 `spawn.rs` 实现超时 + 错误分类。

#### Patch-S3：Sidecar 崩溃自动重启
- **决策**：心跳 15s（3×5s）未达或子进程退出 → 标记 down → 指数退避重启（1s/2s/4s，上限 5 次）。
- **行动**：W1.4 `sidecar/heartbeat.rs` 实现滑动窗口 + watchdog；状态经 Tauri Event 推送。

#### Patch-S4：帧解析失败恢复
- **决策**：
  - malformed JSON → 回送 `-32700 Parse error`（id=null）→ 关闭连接触发重启
  - 长度前缀 >1MB → 读取并丢弃 N 字节 → 回 `-32600` → 保持连接
  - 长度字段与字节流不匹配 → 视为对端崩溃 → 触发重启
- **行动**：W1.3 `rpc.py` + W1.4 `rpc_client.rs` 两侧对称实现。

#### Patch-S5：CSP 显式锁定
- **决策**：`tauri.conf.json` 的 CSP 改为：
  ```
  default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ipc:; img-src 'self' data:
  ```
- **行动**：W1.4 落实。

#### Patch-S6：uptime 精度
- **决策**：`HealthStatus.uptime_seconds: int`（秒），由 `time.monotonic()` 计算；不再用 float。
- **行动**：W1.3 落实。

### 9.3 UX 工程师 P0 修复

#### Patch-U1：首屏 App Shell
- **决策**：W1.5 必须实现 AppShell 骨架（Sidebar 232px + 主区），Sidebar 含 Logo + 5 项占位导航（disabled）+ 底部 engine-state 卡片复用 Prototype 结构；主区 HealthCard 居中。
- **行动**：W1.5 增加 `AppShell.tsx`、`Sidebar.tsx`。

#### Patch-U2：错误结构化分类
- **决策**：`SidecarErrorKind` 枚举（PythonMissing/SpawnFailed/HandshakeTimeout/RpcProtocolError/WorkerCrashed）随错误返回；前端按 Kind 渲染修复指引卡。
- **行动**：W1.4 定义错误枚举 + W1.5 实现 `ErrorGuideCard.tsx`。

#### Patch-U3：状态字段用足
- **决策**：`HealthStatus` 增加 `pid: int`、`last_heartbeat_at: string | null`、`startup_duration_ms: int`、`worker_version: str`、`protocol_version: str`、`degraded_reasons: list[str]`；前端必须渲染 `degraded_reasons` 列表与诊断 footer（PID/最近心跳/启动耗时）。
- **行动**：W1.3 扩展 schema；W1.5 渲染。

#### Patch-U4：README Quickstart 验收
- **决策**：根 README.md 必须在 §5 验收矩阵中列出 "Quickstart ≤ 4 步"，命令 ≤ 4 条；README 必含前置依赖表（Python 3.12+、Node 22、Rust stable）+ 三行命令 + FAQ 链接。
- **行动**：主代理在集成阶段编写 README；§5 验收矩阵追加。

### 9.4 v1.1 补充（P1 选择性吸收）

- **P1-架构-5**：`HealthStatus` 增加 `runtime_info: dict[str, Any]` 字段，Python 实现把 `python_version/sqlite_version` 塞入；保持顶层字段通用。
- **P1-架构-6**：Rust 侧定义 `SidecarError{code,message,retryable,details,correlation_id}`，对齐 SYSTEM_SPEC §16 Error Envelope。
- **P1-安全-11**：会话 token — 启动时 Tauri 生成 32 字节随机 `session_token`，经环境变量传给 sidecar；每个请求 `params._session_token` 校验。
- **P1-UX-2**：useHealthStore 指数退避（ok→5s, degraded→3s, down→1s 起步 30s 封顶）；`document.visibilitychange` 暂停。

### 9.5 v1.1 显式不吸收的项

- **P1-安全-14**（structlog 依赖）：推迟到 Week 2 与日志体系一并设计，W1 用标准 logging 输出 JSON Lines 即可。
- **P1-UX-5**（spawn 搜索顺序）：W1 固定使用 `<repo_root>/.venv/Scripts/python.exe -m worker.runtime`，环境变量覆盖留到 W2。
- **P1-架构-7**（job.cancel 占位）：W1 不实现，W2 与 Command Bus 一并加入。
- **P2-优化** 全部留到 W2 评估。

---

## 10. v1.1 最终 Batch 编排与子代理分配

| Batch | 模块 | 子代理 prompt 文件 | 可并行 |
|---|---|---|---|
| **A** | W1.1 Skeleton + W1.2 Schemas（含 0001/0002/0003 迁移 + DECISIONS.md + 治理文档） | 1 个 general-purpose | 与 B、C 并行 |
| **B1** | W1.3 Python Worker Sidecar（含 rpc/heartbeat/health/lifecycle/commands 占位 + 完整测试） | 1 个 general-purpose | 与 A、B2、C 并行 |
| **B2** | W1.4 Tauri Rust Host（含 spawn/rpc_client/heartbeat watchdog/commands/state/capabilities） | 1 个 general-purpose | 与 A、B1、C 并行 |
| **C** | W1.5 React Frontend Skeleton（AppShell+Sidebar+HealthCard+ErrorGuideCard+tokens.css+stores） | 1 个 general-purpose | 与 A、B1、B2 并行 |

**集成阶段（主代理统一处理）**：
1. 所有 Batch 完成后，主代理在 `apps/desktop/src-tauri/tauri.conf.json` 注册 sidecar 路径
2. 主代理编写根 `README.md`（含 Quickstart ≤4 步）
3. 主代理补充 `.github/workflows/ci.yml` 完整 job
4. 主代理运行联调：`cargo tauri dev` 验证 Gate

---

## 11. v1.1 验收矩阵（增加 UX/DX 项）

| # | 场景 | 通过标准 |
|---|---|---|
| 1 | `python -m worker.runtime` 启动 | 5s 内 stdout 出现 `runtime.ready` notification，含 `protocol_version="1"` |
| 2 | Worker health_check | 返回 `status=ok`、所有 v1.1 新字段（pid/last_heartbeat_at/startup_duration_ms/worker_version/protocol_version/runtime_info） |
| 3 | Worker 心跳 | 启动后 5s/次发送 `runtime.heartbeat` |
| 4 | Worker 优雅退出 | 接收 `runtime.shutdown` 后 2s 内退出码 0 |
| 5 | Worker 畸形帧处理 | malformed JSON → -32700 + 连接关闭；>1MB → -32600 + 丢弃 |
| 6 | `cargo check` (src-tauri) | 0 错误 0 警告 |
| 7 | `cargo clippy` | 无 denied 警告 |
| 8 | `cargo test` (src-tauri) | ≥ 6 个测试通过（含 rpc 帧、并发、错误分类、心跳超时） |
| 9 | `npm run build` (apps/desktop) | 成功产出 dist/ |
| 10 | `tsc --noEmit` | 0 错误 |
| 11 | `cargo tauri dev` 联调 | 窗口打开，显示 AppShell+Sidebar+HealthCard，health=ok，所有 v1.1 字段可见 |
| 12 | Sidecar 启动失败 | 杀掉 Python → 前端展示 PythonMissing 引导卡 |
| 13 | Sidecar 崩溃自愈 | kill -9 Python → 15s 内 watchdog 标记 down → 自动重启 → 恢复 ok |
| 14 | Schema 校验 | 示例 Command/Artifact 通过 ajv/jsonschema |
| 15 | `sqlite3 :memory:` 执行 3 个迁移 | 不报错，外键生效 |
| 16 | CI 占位 | `.github/workflows/ci.yml` 语法正确 |
| 17 | **UX**：HealthCard 颜色变量 | 100% 来自 tokens.css（截图对比 Prototype `.engine-state`） |
| 18 | **UX**：`degraded_reasons` 渲染 | degraded 状态下前端展示原因列表 |
| 19 | **DX**：README Quickstart | ≤ 4 步、≤ 4 条命令、覆盖 Python/Node/Rust 三前置 |
| 20 | **DX**：错误引导卡 | 5 种 SidecarErrorKind 各有对应修复指引 |
