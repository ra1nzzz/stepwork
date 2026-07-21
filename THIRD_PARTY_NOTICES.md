# 第三方声明（Third-Party Notices）

STEPWORK 使用以下第三方开源组件。本文件为**占位版本**，完整清单（含版本号、许可证全文、版权声明）将在 **V0.1 Alpha 发布前**通过自动化工具（`cargo about`、`pip-licenses`、`license-checker`）生成。

## 核心依赖

### 桌面宿主

| 组件 | 用途 | 许可证（预期） |
|---|---|---|
| Tauri | 桌面应用框架 | MIT / Apache-2.0 |
| Rust | 系统编程语言 | MIT / Apache-2.0 |

### 前端

| 组件 | 用途 | 许可证（预期） |
|---|---|---|
| React | UI 库 | MIT |
| TypeScript | 类型系统 | Apache-2.0 |
| Vite | 构建工具 | MIT |
| Zustand | 状态管理 | MIT |
| TanStack Query | 数据获取 | MIT |

### Python Worker

| 组件 | 用途 | 许可证（预期） |
|---|---|---|
| Python | 运行时 | PSF |
| Pydantic | 数据验证 | MIT |
| SQLAlchemy | ORM | MIT |
| Typer | CLI 框架 | MIT |

### 媒体处理

| 组件 | 用途 | 许可证（预期） |
|---|---|---|
| FFmpeg | 音视频处理 | LGPL-2.1 / GPL-2.0 |
| yt-dlp | 视频下载（可选 Provider） | Unlicense |

### 浏览器自动化

| 组件 | 用途 | 许可证（预期） |
|---|---|---|
| Playwright | 浏览器控制 | Apache-2.0 |

## 完整清单待生成

**待办**：

- [ ] 集成 `cargo about` 生成 Rust 依赖清单
- [ ] 集成 `pip-licenses` 生成 Python 依赖清单
- [ ] 集成 `license-checker` 生成 Node 依赖清单
- [ ] 校验所有依赖许可证与 AGPL-3.0 / Apache-2.0 兼容性
- [ ] 输出 `THIRD_PARTY_LICENSES/` 目录，包含每个组件的许可证全文
- [ ] 在 `docs/LICENSE_AUDIT.md` 中记录兼容性结论

**责任**：主维护者在 V0.1 Alpha 发布前完成。

如发现任何许可证遗漏或归属错误，请提交 Issue。
