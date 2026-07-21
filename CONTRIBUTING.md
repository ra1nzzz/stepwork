# Contributing to STEPWORK

感谢您对 STEPWORK 的兴趣。本文档说明如何参与贡献。

## 开发环境

### 前置依赖

| 工具 | 版本 | 用途 |
|---|---|---|
| Python | ≥ 3.12 | Worker / 业务逻辑 |
| Node.js | ≥ 22 | 前端构建 |
| Rust | stable (≥ 1.78) | Tauri 桌面宿主 |
| FFmpeg | ≥ 6.0 | 媒体处理（系统 PATH 或 `STEPWORK_FFMPEG_PATH`） |
| Git | 任意 | 版本控制 |

### 初始化

```bash
git clone https://github.com/ra1nzzz/stepwork.git
cd stepwork

# Python Worker
python -m venv .venv
source .venv/Scripts/activate  # Windows: .venv\Scripts\activate
pip install -e worker[dev]

# 前端
cd apps/desktop && npm install && cd ../..

# Rust
cd apps/desktop/src-tauri && cargo check && cd ../../..
```

### 常用命令

```bash
# Worker 测试
cd worker && pytest

# 前端类型检查
cd apps/desktop && npx tsc --noEmit

# Rust 检查
cd apps/desktop/src-tauri && cargo clippy -- -D warnings
```

## 分支策略

- `main`：受保护分支，始终保持可构建、可发布
- `feature/<short-desc>`：新功能（从 `main` 切出）
- `fix/<short-desc>`：缺陷修复
- `docs/<short-desc>`：纯文档变更
- `chore/<short-desc>`：工具/依赖/构建调整

**禁止**直接向 `main` push；所有变更通过 Pull Request 合入。

## Commit 规范

采用 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/zh-hans/v1.0.0/)：

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Type 枚举

| Type | 含义 |
|---|---|
| `feat` | 新功能 |
| `fix` | 缺陷修复 |
| `docs` | 仅文档 |
| `style` | 格式（不影响代码运行） |
| `refactor` | 重构（既不修 bug 也不加功能） |
| `perf` | 性能优化 |
| `test` | 新增/修改测试 |
| `build` | 构建系统或外部依赖变更 |
| `ci` | CI 配置变更 |
| `chore` | 其他杂项 |
| `revert` | 回滚 |

### Scope 建议

`core` / `worker` / `desktop` / `frontend` / `agent-interop` / `publisher` / `sdk` / `plugins` / `schemas` / `migrations` / `ci` / `docs`

### 示例

```
feat(worker): 添加 runtime.health_check JSON-RPC 方法

实现 HealthStatus schema 与 handler，含 uptime/python_version/sqlite_version 字段。

Refs: W1.3
```

```
fix(schemas): 修正 artifact-envelope producer.type 枚举

新增 "system" 枚举值，对齐 SYSTEM_SPEC §11.1。
```

## Pull Request 流程

1. **Fork & Branch**：从最新 `main` 切出功能分支
2. **开发 + 自测**：本地通过相关 lint 与测试
3. **提交 PR**：
   - 标题遵循 Conventional Commits
   - 描述必填：动机、变更点、测试方式、关联 Issue
   - 链接相关 ADR（如涉及架构决策）
4. **CI 通过**：所有 check 必须绿
5. **Code Review**：至少 1 名维护者 approve
6. **Squash Merge**：默认 squash 合入，保留干净历史

### PR 检查清单

- [ ] 遵循对应语言的代码风格（Python=ruff、TS=eslint、Rust=clippy）
- [ ] 新增功能附带测试
- [ ] 公开 API 变更已更新对应 Schema / SDK 文档
- [ ] 涉及数据库变更已提供 `migrations/NNNN_*.sql`
- [ ] 涉及架构决策已更新或新增 `docs/adr/ADR-XXX`
- [ ] 第三方依赖新增已更新 `THIRD_PARTY_NOTICES.md` 与 `docs/LICENSE_AUDIT.md`

## 许可证与 CLA

提交 PR 即表示您同意所贡献代码按对应模块的许可证发布（详见 [LICENSE](./LICENSE)）。当前不强制 CLA，但项目保留将来引入的权利。

## 行为准则

请遵守 [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)。

## 问题反馈

- Bug / 功能建议：GitHub Issues
- 安全漏洞：**不要公开提 Issue**，请走 [SECURITY.md](./SECURITY.md) 流程
