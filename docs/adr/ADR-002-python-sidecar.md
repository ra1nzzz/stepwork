# ADR-002: 业务逻辑放在 Python Sidecar 而非 Rust

- **Status**: Accepted
- **Date**: 2026-07-21
- **Deciders**: @ra1nzzz

## Context

确定使用 Tauri 后，需要决定业务逻辑（Command Bus、Job Engine、Provider 集成、媒体处理）的宿主语言。候选：

- 全部 Rust：类型安全但生态欠缺（AI SDK、FFmpeg 绑定、Provider SDK 多为 Python 优先）
- 全部 Python：生态丰富但性能弱、打包复杂
- Rust 薄壳 + Python Sidecar：职责分离

关键考量：AI Provider SDK（OpenAI、Anthropic、Gemini）全部 Python 一等公民；FFmpeg Python 绑定成熟；pydantic/SQLAlchemy 是 Python 独有优势。

## Decision

**业务逻辑全部放在 Python 3.12 Sidecar**，Rust 仅做：

- Tauri 主进程（窗口、菜单、托盘）
- Sidecar 生命周期（启动 / 停止 / 重启）
- Capabilities 与权限仲裁
- 系统凭据存储（Stronghold / 系统密钥链）
- 文件对话框

Python Worker 通过 **JSON-RPC over stdio**（长度前缀帧）与 Rust 通信。

## Consequences

**正面**：
- 业务逻辑用 pydantic/SQLAlchemy 建模，开发效率高
- AI Provider SDK 直接复用
- Rust 侧保持极简（约 2000 行），降低维护成本
- Python 解释器与业务代码可独立升级

**负面**：
- 打包体积增加 ~30MB（嵌入式 Python 运行时）
- 跨进程通信开销（但 JSON-RPC 单次 < 1ms，可忽略）
- 需要打包 Python 解释器（用 PyInstaller / Briefcase 或嵌入式 Python）

**关联**：D5 决策的实现基础；SYSTEM_SPEC §3.3 已声明"本地业务层 Python 3.12"。
