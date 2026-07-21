# 许可证审计（LICENSE AUDIT）

**状态**：占位（Placeholder）
**责任**：V0.1 Alpha 发布前完成完整审计
**相关**：[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)

---

## 当前状态

> ⚠️ 本文档为占位版本，列出**已知的第三方组件**及其预期许可证。完整的依赖清单、版本号、许可证全文将在 **V0.1 Alpha 发布前**通过自动化工具生成并审计。

## 已知第三方组件清单

### 桌面宿主层

| 组件 | 预期许可证 | AGPL-3.0 兼容 | Apache-2.0 兼容 | 审计状态 |
|---|---|---|---|---|
| Tauri | MIT / Apache-2.0 | ✅ | ✅ | ⏸ 待确认版本 |
| Rust 工具链 | MIT / Apache-2.0 | ✅ | ✅ | ⏸ 待确认版本 |

### 前端层

| 组件 | 预期许可证 | AGPL-3.0 兼容 | Apache-2.0 兼容 | 审计状态 |
|---|---|---|---|---|
| React | MIT | ✅ | ✅ | ⏸ 待确认版本 |
| TypeScript | Apache-2.0 | ✅ | ✅ | ⏸ 待确认版本 |
| Vite | MIT | ✅ | ✅ | ⏸ 待确认版本 |
| Zustand | MIT | ✅ | ✅ | ⏸ 待确认版本 |
| TanStack Query | MIT | ✅ | ✅ | ⏸ 待确认版本 |
| shadcn/ui | MIT | ✅ | ✅ | ⏸ 待引入 |
| TipTap / Lexical | MIT | ✅ | ✅ | ⏸ 待引入 |

### Python Worker 层

| 组件 | 预期许可证 | AGPL-3.0 兼容 | Apache-2.0 兼容 | 审计状态 |
|---|---|---|---|---|
| Python 运行时 | PSF | ✅ | ✅ | ⏸ 待确认版本 |
| Pydantic | MIT | ✅ | ✅ | ⏸ 待确认版本 |
| SQLAlchemy | MIT | ✅ | ✅ | ⏸ 待确认版本 |
| Typer | MIT | ✅ | ✅ | ⏸ 待确认版本 |

### 媒体处理层

| 组件 | 预期许可证 | AGPL-3.0 兼容 | Apache-2.0 兼容 | 审计状态 |
|---|---|---|---|---|
| FFmpeg | LGPL-2.1 / GPL-2.0 | ⚠️ 动态链接 LGPL 可；GPL 部分禁用 | ⚠️ 同上 | ⏸ 待审计构建配置 |
| yt-dlp | Unlicense | ✅ | ✅ | ⏸ 待确认（可选 Provider） |

### 浏览器自动化层

| 组件 | 预期许可证 | AGPL-3.0 兼容 | Apache-2.0 兼容 | 审计状态 |
|---|---|---|---|---|
| Playwright | Apache-2.0 | ✅ | ✅ | ⏸ 待引入 |

## 已知风险与待确认项

### FFmpeg 许可证风险

FFmpeg 默认构建包含 GPL 组件（libx264、libx265 等）。**必须**：

- 使用 `--enable-lgpl --disable-gpl` 构建的 FFmpeg 二进制
- 仅动态链接（不静态链接）
- 在 `THIRD_PARTY_NOTICES.md` 中明示 FFmpeg 版本与构建配置

**待办**：审计 Windows 平台的 FFmpeg 二进制来源（推荐 gyan.dev 或 BtbN 的 LGPL 构建）。

### media-auto-pilot 依赖

待 `media-auto-pilot` 代码导入后，需要审计其全部依赖：

- [ ] 列出所有 Python 依赖（`requirements.txt` / `pyproject.toml`）
- [ ] 检查每个依赖许可证
- [ ] 标记任何 GPL 依赖（需要替换或移除）
- [ ] 标记任何非标准许可证（Unlicense、WTFPL、CC0 等需逐案评估）

## 审计工具

将在 V0.1 Alpha 前集成以下工具到 CI：

- **Rust**：[`cargo-about`](https://github.com/EmbarkStudios/cargo-about)
- **Python**：[`pip-licenses`](https://github.com/raimon49/pip-licenses)
- **Node**：[`license-checker`](https://github.com/davglass/license-checker)

## 不兼容许可证处置流程

如发现 GPL / SSPL / 其他 Copyleft 强约束依赖：

1. **评估**：是否可替换为兼容实现
2. **隔离**：如必须保留，确保仅通过进程边界调用（不静态/动态链接）
3. **披露**：在 README 与 Release Notes 显著位置披露
4. **决策**：通过 ADR 流程决策是否接受

## 完成标志

- [ ] 所有依赖版本号已锁定并列出
- [ ] 所有依赖许可证已确认并填入上表
- [ ] 不兼容依赖已通过 ADR 决策处置
- [ ] CI 集成许可证扫描，新增依赖自动检查
- [ ] `THIRD_PARTY_LICENSES/` 目录包含所有许可证全文

---

**占位说明**：本文档将在 V0.1 Alpha 发布前由主维护者完成填写。
