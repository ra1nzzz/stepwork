# W5 交付评审（Triple REVIEW Gate）

**日期**：2026-07-21
**里程碑**：STEPWORK W5 — 原创选题角度 + 脚本编辑器（先于 W6 完整交付）
**范式**：YTDEV（7 阶段原子并发开发；W5 由主代理手写 Batch0/1）
**评审门禁**：质量 / 效率 / 复用性 三项均 ≥ 8/10 方可合并

---

## 1. W5 目标与交付物

W5 在 W4（AI Provider + Command Bus）基座之上，补齐**原创选题角度生成**与**脚本编辑器**能力，使「素材 → 选题 → 脚本 → 编辑器润色 → 版本链」形成完整创作链路，并为 W6 视频渲染提供 `script` 内容源。

| # | 交付项 | 状态 | 交付 commit |
|---|---|---|---|
| 计划 | YTDEV W5 执行计划（先于 W6） | ✅ | `a866c56` |
| 后端 | W5 Batch0：TopicProposal/Script 模型 + 3 命令路由 + prompt/parse + handler | ✅ | `4889a69` |
| 契约 | 命令详情回填生成内容（angles / script 供前端直消费） | ✅ | `b6722b5` |
| 前端 | W5 Batch1：TipTap 编辑器 + useScriptStore + 版本链 + 导航路由 | ✅ | `88a55f6` |

---

## 2. Triple REVIEW 评分

### 2.1 质量（Quality）— **9 / 10**

- **模型驱动**：`TopicAngle`/`TopicProposal`/`ScriptSpec` 复用 pydantic v2；`TOPIC_SCHEMA`/`SCRIPT_SCHEMA` 约束 AI 结构化输出，解析层 `parse_topic_proposal`/`parse_script` 做截断与校验（空数组抛错），避免脏数据落库。
- **测试真实性**：`test_topic.py` 端到端覆盖 `GenerateTopic → topic_proposal` 行、`GenerateScript`（proposal → script + parent 链）、`SaveScript`（版本链）。**根因修复**：原测试未先 `repos.workspaces.ensure("ws-t")` 即 `get_or_create_default` → `FOREIGN KEY constraint failed`；补 `_pid()` helper 对齐 handler 的真实调用序。
- **前端类型安全**：`tsc --noEmit` 双 config（app + node）全绿；`payload as unknown as Record<string, unknown>` 正确绕过具名接口缺索引签名的 TS2345/2352，未降级为 `any`。
- **编辑器健壮性**：TipTap `seedBody` 注入时设 `skipNextUpdate` 防 seed 触发 `onUpdate` 回写造成脏保存；0.8s 防抖避免每次按键建版本。
- **扣分项（-1）**：纯浏览器路径走 `tauri.ts` mock（返回示例角度/脚本），真实 `dispatch_command` → Python worker → AI Provider 链路仅以单测覆盖，未跑 `tauri dev` 真机联调。

### 2.2 效率（Efficiency）— **8 / 10**

- **零新增基座**：完全复用 W4 的 `AIProvider.complete(prompt, schema)` 与 Command Bus（`bus.py` 仅加 3 条路由）；**无新增 Rust 命令**、**无新增迁移**、**零硬编码 key**（provider hint 运行时注入）。
- **轻量自动保存**：`SaveScript` 每次仅新建一条 `script` 版本并串 `parent` 链，无全量重写；防抖 0.8s，编辑器无轮询。
- **扣分项（-2）**：(1) 真实模式依赖外部 AI Provider 与密钥，mock 无法验证真实生成质量；(2) 版本链为**前向追加**，当前前端未实现「从历史版本读回 editor」（仅能继续往后存），回滚/对比需后续补。

### 2.3 复用性（Reusability）— **9 / 10**

- **内容模型复用**：选题与脚本均落在既有 `content_versions` 表（无新表），`content_type = "topic_proposal" / "script"`，`parent_version_id` 链天然支持版本树。
- **前端一致性**：`useScriptStore` 与既有 `useTranscriptStore`/`useAnalysisStore` 同构（buildEnvelope + dispatchCommand）；`ScriptView` 三分栏对齐 `Prototype` 栅格；Sidebar 仅加一项 `ViewId` 即接入。
- **契约对称**：`schemas/command-envelope.schema.json` 的 `commandType` 枚举同步扩到 8 类，前后端单一事实源。
- **扣分项（-1）**：`scriptTitle` 当前仅存于 store 状态，未随 `SaveScript` 的 `content`（TipTap doc JSON）一并持久化；如需标题回读需后续把 `{title, doc}` 合并存储。

---

## 3. 验证矩阵（全绿）

| 关卡 | 命令 | 结果 |
|---|---|---|
| Python ruff | `ruff check worker/` | All checks passed |
| Python mypy | `mypy worker/ --no-incremental` | Success, no issues (84 files) |
| Python pytest | `pytest worker/tests` | **64 / 64 通过**（含新增 3 例 test_topic） |
| TS typecheck | `tsc --noEmit -p tsconfig.json` | exit 0 |
| TS typecheck (node) | `tsc --noEmit -p tsconfig.node.json` | exit 0 |

---

## 4. 关键决策与根因

- **选题/脚本落 `content_versions` 而非新表**：W5 是 W4 内容生产链的延伸，复用同一张版本表即可获得 `parent` 链与项目归属，避免 schema 膨胀；W6 的 `RenderSource` 直接 `get(script_version_id)` 消费本链路产物。
- **命令详情回填生成内容（契约补全 `b6722b5`）**：原 `CommandResult.detail` 仅带 `angle_count`/`title`，前端无 content-fetch 接口则拿不到正文。改为 `detail.angles` 带完整角度列表、`detail.script` 带 `{title, body}`，前端渲染/seed 编辑器零额外 RPC。
- **编辑器 seed 防回写**：TipTap `setContent` 会触发 `onUpdate`，若不在 seed 时压 `skipNextUpdate` 标志，会自动保存一份与 AI 稿重复的版本、且会覆盖用户后续编辑的「首存」语义。该标志确保 seed 静默、仅用户真实输入才入版本链。
- **mock 契约对称**：`tauri.ts mockDispatchResult` 对 `GenerateTopic` 返 3 个示例角度、`GenerateScript`/`SaveScript` 返示例脚本，使 W5 在纯浏览器 `npm run dev` 下即可演示完整链路，与 W3/W4 mock 策略一致。

---

## 5. Follow-up / 已知局限

1. **真机联调**：需在 `tauri dev` 下验证 Rust `dispatch_command` → Python worker → AI Provider 的真实选题/脚本生成（当前以单测 + 浏览器 mock 覆盖）。
2. **版本回读**：`VersionHistory` 仅展示链，未实现「点击历史版本载入编辑器」；建议 W5.1 加 `LoadVersion` 命令从 `content_versions` 回填。
3. **标题持久化**：`SaveScript` 当前存 TipTap doc JSON，标题存于 store 状态；建议将 `{title, doc}` 合并为 `content` 以完整回读。
4. **Provider 选择 UI**：W5 复用 W4 provider hint 机制，但前端未暴露「选题/脚本用哪个 provider」的选择器；可在 `TopicView` 顶部接 `ProviderKind` 切换。
5. **W6 衔接**：W5 完成后即可回 W6 前端（`RenderView` 消费 `script` 源），W6 Batch0 后端（`ec6f037`）已就绪。

---

## 6. 评审结论

| 维度 | 评分 | 门禁 |
|---|---|---|
| 质量 Quality | 9/10 | ✅ ≥ 8 |
| 效率 Efficiency | 8/10 | ✅ ≥ 8 |
| 复用性 Reusability | 9/10 | ✅ ≥ 8 |

**Verdict：🔴→🟢 PASS。** 三项均达门禁；W5 后端 + 契约 + 前端全链路闭环，可合并并推送 `origin/main`，随后回 W6。
