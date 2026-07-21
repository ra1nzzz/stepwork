# ADR-001: 桌面宿主采用 Tauri 2 + React

- **Status**: Accepted
- **Date**: 2026-07-21
- **Deciders**: @ra1nzzz

## Context

STEPWORK 需要选择桌面应用框架。候选方案：Electron、Tauri 2、原生（WinUI/Qt）。关键考量因素：

- 安装包体积（内容创作者可能网络条件差）
- 系统资源占用（视频渲染时不能与 Electron 抢内存）
- Web 技术栈复用（团队熟悉 React）
- 跨平台扩展能力（Windows 优先，但需为 macOS 预留）
- 安全模型（前端默认无高权限）

## Decision

采用 **Tauri 2 + React 18 + TypeScript + Vite**。

- 桌面宿主：Tauri 2（Rust 主进程 + WebView2）
- 前端：React 18 + TypeScript + Vite + Zustand + TanStack Query
- UI 组件：shadcn/ui
- 编辑器：TipTap 或 Lexical（最终选型在使用前决定）

## Consequences

**正面**：
- 安装包 ~10MB（Electron 通常 150MB+）
- 内存占用约为 Electron 的 1/3
- Rust 主进程提供强类型 + 系统能力
- WebView2 使用 Chromium，与 Electron 渲染兼容性好

**负面**：
- Rust 学习曲线陡峭（缓解：业务逻辑全部放在 Python Worker，Rust 仅做薄壳）
- WebView2 依赖系统版本（Windows 10 1803+ 已内置，可接受）
- 生态小于 Electron（缓解：核心需求已覆盖）

**关联**：D5（Command Bus 宿主为 Python Worker）使 Rust 侧复杂度最小化。
