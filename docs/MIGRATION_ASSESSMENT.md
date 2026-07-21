# Media Auto Pilot 迁移审计（MIGRATION ASSESSMENT）

**状态**：占位（Placeholder）
**责任**：Week 1 后续完成
**决策依据**：SYSTEM_SPEC §20 迁移矩阵

---

## 当前状态

> ⚠️ **`media-auto-pilot` 代码尚未导入本仓库**，审计工作将在 Week 1 后期（代码导入后）完成。本文档列出**待审计项清单**作为占位。

## 待审计模块清单

依据 SYSTEM_SPEC §20 迁移矩阵，逐项评估并标记为 **保留 / 重写保留 / 重构复用 / 替换 / 迁移为插件 / 删除**。

| 模块 | 初始决策 | 审计要点 | 状态 |
|---|---|---|---|
| ChromeProcess | 重写后保留 | 跨平台路径、动态端口分配、Profile 隔离机制、异常退出清理 | ⏸ 待审计 |
| BrowserSession | 重构复用 | CDP 持久连接思想、Session 复用策略、断线重连 | ⏸ 待审计 |
| DOMSnapshot | 重构复用 | 增加 iframe / Shadow DOM 支持、快照版本管理 | ⏸ 待审计 |
| SmartLocator | 重构复用 | 多策略定位算法；**禁止用于无确认不可逆操作** | ⏸ 待审计 |
| FileUploader | 重构复用 | 修复上传验证（大小/类型/内容校验）、进度上报 | ⏸ 待审计 |
| BasePlatform | 替换为 PublisherAdapter | 强类型接口、结构化状态机（OPEN_ONLY/FILL_AND_PREVIEW/CONFIRMED_PUBLISH） | ⏸ 待审计 |
| DouyinPlatform | 迁移为官方插件 | 默认 Fill and Preview 模式；**不得自动点击最终发布** | ⏸ 待审计 |
| FrequencyController | 删除规避导向逻辑 | 仅保留正常速率限制（如 1 req/s）；**删除多账号轮换、UA 轮换等规避风控代码** | ⏸ 待审计 |
| Stealth scripts | 删除 | **不进入正式产品**；标记为 archived，不进入 git 主分支 | ⏸ 待审计 |
| CLI | 保留诊断用途 | 仅用于诊断；正式调用走统一 Command Bus | ⏸ 待审计 |

## 删除项确认清单

根据 SYSTEM_SPEC §14.6，以下内容**不得**进入 STEPWORK：

- [ ] Canvas/WebGL 指纹伪装代码
- [ ] 隐藏 WebDriver 的反检测脚本（`navigator.webdriver = undefined` 等）
- [ ] 多账号轮换规避风控逻辑
- [ ] 无确认最终发布代码路径
- [ ] 默认 `--no-sandbox` 启动参数
- [ ] User-Agent 轮换池
- [ ] 代理 IP 轮换池
- [ ] Cookie 池 / 凭据共享

## 审计步骤（代码导入后）

1. **导入代码**：将 `media-auto-pilot` 复制到 `archive/media-auto-pilot/`（不进入主构建路径）
2. **逐文件审计**：按上表逐项标记决策，填写"审计要点"列
3. **删除项隔离**：将上表"删除项确认清单"中的代码**物理移除**或标记为 `# REMOVED: <reason>`
4. **许可证检查**：确认 media-auto-pilot 的 LICENSE 允许迁移到 AGPL-3.0 STEPWORK
5. **依赖审计**：更新 `docs/LICENSE_AUDIT.md`，纳入 media-auto-pilot 的全部依赖
6. **接口设计**：为标记"重构复用"的模块设计 Python Protocol 接口
7. **重构计划**：在 `publisher-engine/` 中建立新模块骨架，将可复用代码逐步迁移

## 风险

| 风险 | 概率 | 应对 |
|---|---|---|
| media-auto-pilot 许可证与 AGPL 不兼容 | 低 | 替换为自研；联系原作者获取双重许可 |
| 删除反检测代码后发布成功率显著下降 | 中 | 接受下降，明示用户"合规优先"；提供 Fill & Preview 让用户手动确认 |
| ChromeProcess 跨平台兼容性差 | 中 | 重写时引入 Playwright 作为底层，替换直接 CDP |

## 完成标志

- [ ] 所有待审计项的"状态"列已填写
- [ ] 删除项已物理移除并注明理由
- [ ] `docs/LICENSE_AUDIT.md` 已更新
- [ ] `publisher-engine/` 骨架已建立（Week 1 Gate 不做要求，Week 2-3 完成）

---

**占位说明**：本文档将在代码导入后一周内完成填写。
