# ADR-009: 第三方插件运行在独立进程

- **Status**: Accepted
- **Date**: 2026-07-21
- **Deciders**: @ra1nzzz

## Context

STEPWORK 插件系统需要决定第三方代码的运行位置。候选：

- **同进程加载**（如 VS Code 扩展）：性能最好，但插件崩溃会拖垮整个应用，且无法隔离权限
- **独立进程 + RPC**（如 Figma 插件、Obsidian 插件）：隔离性好，但通信开销
- **Web Worker / iframe**：仅适用于 UI 插件，无法访问系统能力

STEPWORK 插件涉及：调用外部 API（AI Provider）、运行 FFmpeg、读写项目文件。风险高。

## Decision

**所有第三方插件运行在独立进程**（SYSTEM_SPEC §12.5）：

- Python 插件：独立 Python 子进程，通过 JSON-RPC 与 Worker 通信
- Node 插件：独立 Node 子进程，同上
- **不允许任意继承父进程环境变量**（白名单传递）
- 凭据通过**受控 RPC 短期句柄**获取，插件无法直接访问系统密钥链
- 高权限插件**不可加载远程代码**（`exec()` / `eval()` / 动态 `import` from URL 均被禁止）
- 插件输出必须经过 Schema 验证才能进入 Artifact 链

## Consequences

**正面**：
- 插件崩溃不影响主应用（Worker 检测到子进程退出后标记插件 unhealthy）
- 权限最小化（每个插件独立 OS 用户 / seccomp 可后续叠加）
- 凭据安全（短期 token + RPC 边界）
- 可独立升级 / 禁用 / 卸载

**负面**：
- 进程间通信开销（JSON-RPC 单次 ~1ms，可接受）
- 内存占用增加（每个插件 ~30-50MB Python 解释器）
- 调试复杂（需要附加到子进程）

**关联**：SYSTEM_SPEC §12 完整定义；ADR-002 Python Sidecar 已确立跨进程模式。
