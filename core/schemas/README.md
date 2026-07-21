# core/schemas/ · Schema 引用说明

> **本目录不存放任何 Schema 副本。**

## 唯一事实源

STEPWORK 的所有 JSON Schema 唯一存放在仓库根的 [`schemas/`](../../schemas/) 目录：

- [`schemas/command-envelope.schema.json`](../../schemas/command-envelope.schema.json)
- [`schemas/artifact-envelope.schema.json`](../../schemas/artifact-envelope.schema.json)
- [`schemas/job-state.enum.json`](../../schemas/job-state.enum.json)
- [`schemas/job-stage.enum.json`](../../schemas/job-stage.enum.json)
- [`schemas/error-envelope.schema.json`](../../schemas/error-envelope.schema.json)

## 为什么 core/schemas/ 不建副本

W1_MONOREPO_PLAN v1.1 Patch-A1 决定：

> Schema 唯一存放 `schemas/`（根目录），`core/schemas/` 不建独立副本，仅以 `README.md` 说明"引用根 schemas/"。

**理由**：

- 避免软链 / 复制导致的双源漂移
- Rust 通过 `include_str!` 在编译时嵌入根 `schemas/`
- Python 通过 `importlib.resources` 或构建脚本从根 `schemas/` 读取
- TypeScript 通过 Vite `?raw` 导入根 `schemas/`

## 本目录的角色

`core/schemas/` 保留此目录仅为：

1. 对齐 SYSTEM_SPEC §5 的目录结构（`core/` 下声明了 `schemas/` 子目录）
2. 通过本 README 指引开发者去正确的位置
3. 未来可能存放**派生**产物（如从根 Schema 自动生成的 Rust/Python 类型定义），但**永不存放 Schema 本体**

## 使用方式

参见 [`schemas/README.md`](../../schemas/README.md) 获取 Rust / Python / TypeScript 三种语言的具体引用代码示例。
