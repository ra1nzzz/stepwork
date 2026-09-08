# STEPWORK 升级指南（W9 L.44）

> **适用版本**：V0.1 Windows Alpha → RC1（2026-07-24）
> **平台**：Windows + PowerShell（macOS/Linux 推 V0.2）

---

## 1. 升级方式

STEPWORK 目前采用**源码升级**（`git pull` + `pip install -e .`），不支持
自动在线更新（推 V0.2）。

### 1.1 标准升级流程

```powershell
# 1. 进入仓库根目录
cd D:\Code\STEPWORK

# 2. 拉取最新代码
git pull

# 3. 更新依赖（venv 已存在，直接 install 覆盖）
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" --upgrade

# 4. 跑迁移（bootstrap_db 会在下次启动时自动跑，这里手动跑确认无报错）
.\.venv\Scripts\python.exe -m worker.runtime.__main__ --migrate-only
# 若无 --migrate-only 参数，启动 worker 即自动迁移

# 5. 验证测试
.\.venv\Scripts\python.exe -m pytest worker\tests -q
```

### 1.2 升级前备份

升级前**强烈建议**手动备份工作区数据：

```powershell
# 方式 1：用 W9 备份命令（推荐，走命令总线）
.\.venv\Scripts\python.exe -c "import asyncio; from worker.runtime.app import run_command; import json; r = asyncio.run(run_command(json.loads('{\"commandId\":\"cmd-backup\",\"commandType\":\"BackupWorkspace\",\"schemaVersion\":\"1\",\"actor\":{\"type\":\"user\",\"id\":\"u1\"},\"source\":\"cli\",\"workspaceId\":\"ws-local\",\"payload\":{\"label\":\"pre-upgrade\"},\"requestedAt\":\"2026-07-24T00:00:00+00:00\"}'))); print(r)"

# 方式 2：直接拷贝数据库文件（最简单）
Copy-Item $env:STEPWORK_HOME\stepwork.db "$env:STEPWORK_HOME\stepwork-pre-upgrade.db"
```

备份文件落在 `$STEPWORK_HOME\backups\`。

---

## 2. 版本兼容性

### 2.1 数据库迁移

- migrations/ 目录的 SQL 脚本**只增不改**（向前兼容）。
- `bootstrap_db` 启动时自动按序执行未应用的迁移。
- 生产路径下，迁移前自动备份 `stepwork.db` 到 `backups/stepwork-<timestamp>.db`
  （见 `worker/runtime/bootstrap.py`）。
- 若迁移失败，回滚策略见 `migrations/README.md`：用备份文件覆盖即可。

### 2.2 命令信封 schema

- `schemas/command-envelope.schema.json` 的 `commandType` enum **只增不删**。
- 新增命令不会破坏旧前端（旧前端不会发新命令）。
- 跨大版本（V0.x → V1.0）可能调整 `schemaVersion`，届时提供适配层。

### 2.3 配置兼容

- `Workspace.settings`（JSON 列）向前兼容：新增字段默认值在 handler 层补齐。
- 密钥覆盖层（内存）不持久化，升级后需在设置页重新输入（设计如此，安全考虑）。

---

## 3. 回滚

升级后若发现回归，可回滚到上一个版本：

```powershell
# 1. 停止 worker（若在运行）

# 2. 回滚代码
git checkout <previous-tag>   # 如 v0.1-rc0

# 3. 恢复数据库（用升级前的备份）
Copy-Item "$env:STEPWORK_HOME\backups\stepwork-pre-upgrade.db" $env:STEPWORK_HOME\stepwork.db -Force

# 4. 重装依赖（若依赖版本变了）
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 5. 验证
.\.venv\Scripts\python.exe -m pytest worker\tests -q
```

**注意**：数据库回滚后，新版本写入的数据会丢失。若升级期间产生了重要数据，
先导出（`ExportProject`）再回滚。

---

## 4. 从头安装

```powershell
# 1. 克隆仓库
git clone <repo-url> D:\Code\STEPWORK
cd D:\Code\STEPWORK

# 2. 跑安装脚本（检查 Python 3.12+ → 创建 .venv → 装依赖）
.\scripts\install.ps1

# 3. 注入种子数据（5 个示例项目）
.\.venv\Scripts\python.exe scripts\seed_demo.py

# 4. 启动
.\.venv\Scripts\python.exe -m worker.runtime.__main__
```

---

## 5. 卸载

```powershell
# 仅删 .venv（保留用户数据）
.\scripts\uninstall.ps1

# 彻底清除（含 $STEPWORK_HOME 数据库/日志/备份）
.\scripts\uninstall.ps1 -RemoveData
```

---

## 6. 常见问题

### Q: 升级后 worker 启动报 `no such table: xxx`

迁移未执行。手动跑：

```powershell
.\.venv\Scripts\python.exe -c "from worker.runtime.bootstrap import bootstrap_db; from worker.runtime.state import WorkerState; bootstrap_db(WorkerState())"
```

### Q: 升级后 CLI 报 `commandType not in enum`

前端缓存了旧 schema。重启 Tauri 桌面应用，或清除前端缓存：

```powershell
Remove-Item "$env:APPDATA\stepwork\*" -Recurse -Force
```

### Q: `pip install` 报权限错误

确保 PowerShell 以管理员身份运行，或用 `--user`：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" --user
```

### Q: 升级后测试失败

先确认是否为 perf 测试（默认不跑）：

```powershell
.\.venv\Scripts\python.exe -m pytest worker\tests -m "not perf" -q
```

若仍失败，查看 `docs/PERF_BASELINE.md` 确认是否性能回归，或提交 issue 附上
完整错误日志。

---

## 7. 版本历史

| 版本 | 日期 | 说明 |
|---|---|---|
| V0.1-RC1 | 2026-07-24 | W9 收口：E2E + 导出导入 + 备份恢复 + 日志落盘 + 性能基线 + 种子数据 + 安装脚本 |
| V0.1-RC0 | 2026-07-23 | W8 收口：插件系统 + Provenance + Agent 任务 + 诊断包 |
| V0.1-beta | 2026-07-15 | W3-W7：Import/Transcribe/Analyze/Topic/Script/Render 全链路 |
