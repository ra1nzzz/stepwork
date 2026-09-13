# stepwork-worker

STEPWORK Python Worker Sidecar（W1.3）。

## 概述

`stepwork-worker` 是 STEPWORK 桌面应用的内容处理 Sidecar，通过 **stdio 上的长度前缀 JSON-RPC 2.0** 与 Tauri Rust Host 通信。

W1 范围实现：

- `runtime.ready` notification（协议版本协商 + capabilities 声明）
- `runtime.heartbeat` notification（每 5 秒）
- `runtime.health_check` 请求-响应（含 v1.1 全部诊断字段）
- `runtime.shutdown` 请求-响应（优雅退出）
- `job.*` 占位（W2 与 Command Bus 一并实现）

## 帧格式

```
[4 字节大端 uint32 长度][JSON UTF-8 字节流]
```

- 最大帧长：`1 MiB`（超出回 `-32600` 并丢弃载荷）
- 畸形 JSON：回 `-32700`（id=null）并关闭连接
- 长度与实际字节流不匹配：视为对端崩溃，关闭连接

## 快速开始

```bash
# 在仓库根目录
python -m venv .venv
.venv/Scripts/pip install -e worker[dev]

# 启动 sidecar（stdout 输出 JSON-RPC 帧）
.venv/Scripts/python -m worker.runtime
```

## 开发

```bash
# 测试
.venv/Scripts/python -m pytest worker/tests

# 覆盖率
.venv/Scripts/python -m pytest worker/tests --cov=worker/runtime --cov-report=term-missing

# Lint
.venv/Scripts/ruff check worker/

# 类型检查
.venv/Scripts/mypy worker/runtime
```

## 打包（单文件侧车，Tauri `externalBin` 的目标）

```powershell
# 在仓库根目录。产物 dist/worker-sidecar/stepwork-worker.exe
# 并自动投递为 apps/desktop/src-tauri/binaries/stepwork-worker-<triple>.exe
pwsh scripts\build_worker_sidecar.ps1

# 验收（冻结态资源自检 + 真实 JSON-RPC 链路）
python .workbuddy\packaging-acceptance\accept_sidecar.py

# 排障：在冻结进程里看随包资源找没找到
dist\worker-sidecar\stepwork-worker.exe --selfcheck
```

打包配置是**签入仓库**的 `packaging/stepwork-worker.spec`：

- `datas` 由 `worker.runtime.assets.bundled_datas()` 生成，与运行期
  `repo_path()` 的读取点**同源** —— 漏打资源是静默的（字体丢了只是字形变回
  系统字体），所以清单不能靠人工核对。
- `hiddenimports` 用 `collect_submodules("worker")` 整包收：`bus.py` 用
  `importlib.import_module(module_path)` 路由，模块名来自 `_ROUTES` **字典**
  而非 import 语句，静态分析看不见。
- 可选引擎默认排除，用 `STEPWORK_BUNDLE_RENDER` / `_ASR` / `_TTS` 单独开。
  关掉时对应 provider 返 `None` → `UNAVAILABLE`（**不会**静默换 ffmpeg 渲出
  另一条片子）。playwright 的浏览器二进制 pip 装不了，开了也仍需
  `playwright install chromium`。

## 协议方法（W1 最小集）

| Method | Params | Result | 类型 |
|---|---|---|---|
| `runtime.ready` | — | `{ready, pid, started_at, protocol_version, capabilities, worker_version}` | notification |
| `runtime.heartbeat` | — | `{alive, timestamp}` | notification（每 5s） |
| `runtime.health_check` | `{}` | `HealthStatus` | request-response |
| `runtime.shutdown` | `{graceful: bool}` | `{bye: true}` | request-response |
| `job.*` | — | `-32601 Method not implemented in W1` | 占位 |

## HealthStatus 字段（v1.1）

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | `"ok" \| "degraded" \| "down"` | 运行状态 |
| `version` | `str` | worker 版本 |
| `protocol_version` | `str` | 协议版本（当前 `"1"`） |
| `uptime_seconds` | `int` | `time.monotonic()` 计算的整数秒 |
| `pid` | `int` | 进程 ID |
| `last_heartbeat_at` | `str \| null` | 最近心跳 ISO-8601 |
| `startup_duration_ms` | `int` | 启动耗时（毫秒） |
| `active_jobs` | `int` | 活跃任务数 |
| `degraded_reasons` | `list[str]` | 降级原因列表 |
| `runtime_info` | `dict` | `python_version` / `sqlite_version` / `platform` |

## 环境变量

| 变量 | 说明 |
|---|---|
| `STEPWORK_SESSION_TOKEN` | Tauri 生成的 32 字节会话 token；缺省时 worker 自动生成 uuid4 hex |

## 许可

AGPL-3.0-or-later（Core）
