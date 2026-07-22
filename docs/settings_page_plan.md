# STEPWORK · 设置中心（Settings Hub）实现计划

> YTDEV 范式 · Phase 2/4 PLAN
> 目标：让 `apps/desktop` 对齐原型 `Prototype/settings.html` 的"05 设置"页，覆盖 LLM/ASR/TTS/存储/Brand/导入导出等全部配置，持久化，并打通真实 worker 后端（经 dev-bridge / Tauri invoke）。
> 日期：2026-07-23 ｜主理人：沽思航（gstack-lead）

---

## 0. 现状审计结论（Phase 1 调研，证据确凿）

| 端 | 现状 | 证据 |
|----|------|------|
| 前端 | 无路由库；`useViewStore` 切视图（ViewId=`home\|import\|transcript\|analysis\|script\|render`）；右侧内容区骨架在，但**默认 dev 全 mock**（`tauri.ts:189-253`）；**完全没有设置/偏好层**（无 SettingsView、无 settings store、全项目无 localStorage/persist） | `useViewStore.ts:8`、`tauri.ts:87-91,189-253`、`useAnalysisStore.ts:9`（注释"api_key 绝不落库"） |
| 原型 | `settings.html` 已定义完整 4 分类设置页（BrandProfile / AI Provider / 数据与存储 / 导入与导出），导航固定 `05 设置`，`data-check-config` 按钮 + `app.js` toast/tab 引擎已就绪 | `Prototype/settings.html:39-43,107,112,117` |
| 后端 | 配置**全靠 `os.environ`**，无 pydantic-settings / 无配置文件；provider/model 来自 env（`STEPWORK_AI_*`/`STEPWORK_OPENAI_*`/`STEPWORK_ASR_*`/`STEPWORK_TTS_*`）+ 请求体 hint 双通道；`bus.py:19-28` 路由**无任何 Config 命令**；`Workspace.settings` 列已存在但从未读写；密钥明文 env，无加密/keychain | `worker/runtime/providers/resolve.py:35,41,60-84,87-112,115-131`、`bus.py:19-28`、`repos.py:126-129` |

**关键缺口**：前端无设置页 → 后端无配置读写命令。两端都要补。

---

## 1. 需要进"设置"的配置项清单（用户要的"全面梳理"）

> 来源：✓=原型 `settings.html` 明确出现；⊕=从后端 env 面/常识推断应补。

### A. AI Provider（LLM + 语音）— 用户已点名
- ✓ 文本服务商 `aiProvider`：`cloud` / `openai-compatible` / `ollama` → `STEPWORK_AI_PROVIDER`（resolve 支持三者）
- ✓ 文本模型 `aiModel`（如 `STEPFUN step-3.7`）→ `STEPWORK_AI_MODEL`
- ✓ API Key `aiApiKey` → `STEPWORK_AI_API_KEY`　**【密钥·敏感】**
- ✓ Base URL `aiBaseUrl` → `STEPWORK_AI_BASE_URL`
- ⊕ 成本单价 `aiCostPer1k` → `STEPWORK_AI_COST_PER_1K`（可选）
- ✓ ASR 服务商 `asrProvider`：`local` / `cloud` → `STEPWORK_ASR_PROVIDER`
- ✓ ASR Key `asrApiKey` → `STEPWORK_ASR_API_KEY`　**【密钥】**
- ✓ ASR Base URL `asrBaseUrl` → `STEPWORK_ASR_BASE_URL`
- ✓ TTS 服务商 `ttsProvider`：`local` / `cloud`（原型 `StepAudio`/`Edge TTS`）→ `STEPWORK_TTS_PROVIDER`
- ✓ TTS Key `ttsApiKey` → `STEPWORK_TTS_API_KEY`　**【密钥】**
- ✓ TTS Base URL `ttsBaseUrl` → `STEPWORK_TTS_BASE_URL`
- ✓ TTS 模型/音色 `ttsModel` → `STEPWORK_TTS_MODEL`
- ⊕ 采样参数 `temperature` / `topP` / `maxTokens`（影响生成质量；当前 `resolve_ai` 仅用 env model，需 hint 扩展）
- ✓ "每次任务前展示模型/费用/上传范围"（`data-check-config` 按钮的语义）

### B. BrandProfile（原创角度/脚本语气约束）— 原型明确
- ✓ 配置名称 `brandName`（如 `科技实测·克制判断`）
- ✓ 核心受众 `audience`
- ✓ 表达原则 `tone`
- ✓ 必须执行项 `mustExecute[]`：标注来源时间戳 / 查历史相似度 / 高风险人工确认
- ✓ 默认输出 `defaultOutput[]`：≤90s / 9:16 竖屏 / 口播+B-roll

### C. 数据与存储 — 原型明确（用户点名的"项目默认文件夹"在此）
- ✓ **项目默认文件夹 / 本地项目空间 `workspaceDefaultPath`** → `STEPWORK_HOME`（默认 `~/STEPWORK`，workspace root 默认 `STEPWORK_HOME/workspaces/<id>`）
- ✓ 素材与 Artifact 存储位置（per workspace）
- ✓ 项目级删除 `projectDelete`
- ✓ 30 天任务日志保留 `logRetentionDays=30`
- ✓ 诊断包脱敏 `diagnosticDesensitize`
- ✓ 上传范围 `uploadScope`（数据边界）

### D. 导入与导出 — 原型明确
- ✓ 项目包包含（素材清单 / ContentVersion / Provenance / 渲染记录）
- ✓ 导出前检查缺失依赖 `exportDependencyCheck`

### E. 推断应补（⊕，提升完成度，非阻塞）
- ⊕ 主题 `theme`（深/浅，纯前端）
- ⊕ 界面语言 `language`
- ⊕ 日志级别 `logLevel`
- ⊕ 默认导出格式 `exportFormat`（MP4/SRT/WAV）+ 路径
- ⊕ 默认竖屏模板 `defaultTemplate=9:16`
- ⊕ 背景音乐开关 `bgmToggle`、字幕样式 `subtitleStyle`
- ⊕ Session Token `STEPWORK_SESSION_TOKEN`（高级）
- ⊕ FFmpeg 可执行路径 `FFMPEG_BIN`（高级，render 用）

---

## 2. 模块拆解（全局唯一前缀 `SET.`，原子、文件无交叉）

| 模块 | 端 | 文件（新建/改） | 职责 |
|------|----|----------------|------|
| `SET.1` | FE | `src/stores/useViewStore.ts` / `src/components/Sidebar.tsx` / `src/App.tsx` | ViewId 加 `"settings"`；Sidebar 加 `05 设置` 项；App switch 加 `case "settings"` |
| `SET.2` | FE | `src/stores/useSettingsStore.ts`（新） | zustand + `persist`（localStorage）；形状=上表 A–E；`load/save/reset/exportEnvPatch()` |
| `SET.3` | FE | `src/features/settings/SettingsView.tsx`（新）+ 子区块 | 4 Tab（Brand/AI/Data/Export）对齐原型；复用 `tokens.css`+`.feature-view`；`检查当前配置`→GetConfig；保存→UpdateConfig |
| `SET.4` | FE | `src/lib/tauri.ts` | 加 `getConfig()`/`updateConfig(patch)`：DEV_BRIDGE(fetch POST /dispatch Get/UpdateConfig) / Tauri(invoke) / Mock(本地) 三分支 |
| `SET.5` | BE | `worker/runtime/commands/bus.py` + `worker/runtime/handlers/config.py`（新） | `_ROUTES` 加 `GetConfig`/`UpdateConfig`；handler 返回合并视图（env+Workspace.settings+runtime hint）；写 `Workspace.settings`（非密钥）+ 推密钥到内存覆盖层 |
| `SET.6` | BE | `worker/runtime/providers/resolve.py` | 加 `CONFIG_OVERRIDES` 内存注册表，在 `resolve_ai/asr/tts` 中**优先于 env** 读取；UpdateConfig 填充（仅密钥，绝不落库） |
| `SET.7` | FE+BE | `src/lib/types.ts`（L274 附近）/ `worker/runtime/models.py` | 前端 `SettingsConfig`/`ConfigView`；后端 `ConfigSpec`/`ConfigView` pydantic |

### Batch 编排（无文件交叉）
- **Batch A（前端 UI，独立文件）**：`SET.1`（useViewStore+Sidebar+App）、`SET.2`（useSettingsStore 新）、`SET.3`（SettingsView 新）
- **Batch B（前端桥，单文件共享）**：`SET.4`（tauri.ts）——主代理统一集成（其它视图也用它）
- **Batch C（后端）**：`SET.5`（bus.py 路由 + handlers/config.py 新）、`SET.6`（resolve.py 覆盖层）、`SET.7`（models.py ConfigView）
- **共享文件主代理统一集成**：`tauri.ts`(SET.4)、`bus.py`(SET.5 路由)、`resolve.py`(SET.6)、`types.ts`(SET.7 FE)、`models.py`(SET.7 BE)

### 依赖
- `SET.3` 依赖 `SET.1`（视图切换）+ `SET.2`（store）
- `SET.7` 类型须先于 `SET.3`/`SET.4` 落地（TS 编译）
- `SET.4` 真实模式依赖 `SET.5`/`SET.6`（后端命令存在）；Mock 模式可独立

---

## 3. 多角色头脑风暴结论（Phase 3 → P0/P1 已并入 v1.1）

三并发角色（产品评审=架构师 / 安全官 / 设计顾问）已评审 v1，P0 必改如下：

1. **单真源**：`useSettingsStore` 为唯一运行时真源；`tauri.ts` Mock 分支不得自管 localStorage，改代理 store 的 getState/setState。
2. **密钥绝不落浏览器**：`persist` 的 `partialize` 剔除所有 `*Key`/`*Secret`；前端只持 `configured:true`，密钥仅内存态、SET.4 只上行不回显。
3. **GetConfig 兑现"检查当前配置"**：`ConfigView = defaults < env < Workspace.settings < 内存覆盖层`，返回**掩码** `hasApiKey`、绝不回明文。
4. **UpdateConfig + 覆盖层同原子提交** + 冒烟（dispatch UpdateConfig → `resolve_ai` 读覆盖值）；覆盖层**按 `workspace_id` 隔离** + 原子替换 + 锁。
5. **`Workspace.settings` 定 `ConfigSpec`**（pydantic 默认值/校验），GetConfig 合并返回，防旧库 KeyError。
6. **安全加固（沙箱本机可接受·生产必须）**：dev-bridge CORS 收窄到 dev origin + 共享密钥 header，绑定 `127.0.0.1` 并断言禁 `0.0.0.0`；`UpdateConfig` 路由层加 actor 白名单 `{user, desktop}`。
7. **CSS 移植**：把原型 `settings.css` 的 `.settings-menu`/`.panel`/`.field`/`.chip`/`.empty` 搬进前端 `styles/`（原计划 `.feature-view` 是幽灵类，弃用）；首屏未配置显 `degraded` 状态；保存补失败态 + `aria-busy`；校验错误译人话用现有 `.error-guide-card`；API Key 用 `type=password` + 掩码回显。

P1（建议改，不阻塞）：`SettingsConfig` 组合复用 `ProviderConfig`(types.ts:274) 去重；provider 改 `Record<ServiceType, ProviderConfig>` 映射提升扩展性；`ConfigView` 加契约测试防前后端漂移；补 `import` 配置对齐原型；Mock↔桥↔生产三层存储显式优先级 + 切回一次性清理。



1. **密钥落存模型（安全·关键）**：API Key 绝不能明文进 SQLite。`SET.5/SET.6` 方案 = UpdateConfig 把密钥只存**内存覆盖层**（`resolve.py` 运行期读取），持久化只写 `Workspace.settings` 的非密钥字段；前端 `persist` 排除 `*.apiKey` 字段（或单独存、标注风险）。备选：系统 keychain（重，v1 不做）。
2. **配置生效通道**：前端 Save → `UpdateConfig` → 后端写内存覆盖层 + Workspace.settings；后续 `resolve_ai/asr/tts` 优先读覆盖层（已在 `resolve.py` 预留 hint 通道）。无需改 env 文件。
3. **v1 范围**：A（LLM+ASR+TTS 全）+ B（Brand）+ C（存储/默认文件夹）+ D（导入导出）为**必做**；E（主题/语言/日志/导出格式等）为**选做**（建议 v1 含主题+语言，其余留 v2）。
4. **"检查当前配置"按钮**：`GetConfig` 返回已解析的 provider 状态（来自 env+覆盖层），前端渲染到 `data-check-config` 弹层，对齐原型语义。

---

## 4. 验收（Phase 6 门禁）
- 质量/效率/可复用性 ≥ 8/10（三重并行 REVIEW）
- 前端 `tsc --noEmit -p tsconfig.json` strict exit 0
- 后端 `pytest worker/tests` 全过 + `ruff`/`mypy` clean
- e2e：dev-bridge 起 → GUI `VITE_DEV_BRIDGE=1` 打开设置页 → 改 `aiModel` 保存 → `GetConfig` 回显新值 → 真实 `GenerateTopic` 用新模型 hint 命中 handler
- 密钥字段不出现在 `Workspace.settings` 的持久化内容中（grep 校验）

---

## 5. 实现状态（Phase 5 完成 · 2026-07-23）

### 已落地的代码（主理人统一集成共享文件）
| 模块 | 文件 | 状态 |
|------|------|------|
| 前端路由/导航 | `apps/desktop/src/stores/useViewStore.ts`、`Sidebar.tsx`、`App.tsx` | SET.1 子代理完成 |
| 前端 store | `apps/desktop/src/stores/useSettingsStore.ts`（zustand+persist，partialize 剔除 `*Key`/`*Secret`） | SET.2 子代理完成 |
| 前端 UI | `apps/desktop/src/features/settings/SettingsView.tsx` + `styles/settings.css` | SET.3 子代理完成 |
| 前端桥 | `apps/desktop/src/lib/tauri.ts` 加 `getConfig()`/`updateConfig()`（三分支：DEV_BRIDGE / Tauri / Mock 代理 store） | SET.4 主理人 |
| 前端类型 | `apps/desktop/src/lib/types.ts` 加 `ConfigView`/`ConfigResult` | SET.7 主理人 |
| 后端命令 | `worker/runtime/handlers/config.py`（NEW）+ `bus.py` 路由 + actor 白名单 `{user,desktop}` | SET.5 主理人 |
| 后端密钥层 | `worker/runtime/providers/resolve.py` 加 `CONFIG_OVERRIDES`（按 workspace_id 隔离 + `threading.Lock`），`resolve_ai/asr/tts(workspace_id)` 优先读覆盖层 | SET.6 主理人 |
| 后端模型 | `worker/runtime/models.py` 加 `ConfigSpec`/`ConfigView` | SET.7 主理人 |
| 后端仓储 | `worker/runtime/db/repos.py` 加 `WorkspaceRepo.update_settings` | SET.5 主理人 |
| 后端注入 | `worker/runtime/handlers/commands.py` 把 `envelope.workspaceId` 传给 `resolve_*` | SET.5 主理人 |
| 安全加固 | `worker/dev_bridge.py` CORS 收窄到 `localhost:1420`/`127.0.0.1:1420` | SET.5 P0 |
| 测试 | `worker/tests/test_config_command.py`（NEW：默认/掩码、非密钥落库+密钥进覆盖层、FORBIDDEN_ACTOR、覆盖层驱动 resolve_ai） | SET.5 主理人 |

### 密钥安全模型（已落地·三重保险）
1. **浏览器永不持明文**：`useSettingsStore` 的 `persist.partialize` 用 `stripSecrets()` 递归剔除所有 `*Key`/`*Secret` → localStorage 中无密钥。
2. **磁盘永不落明文**：`handlers/config.py` 的 `_strip_secrets()` 在写 `Workspace.settings` 前剥离密钥字段 → SQLite 中无密钥。
3. **仅进程内存**：密钥只进 `resolve.CONFIG_OVERRIDES`（按 workspace_id 隔离 + 全局锁），worker 重启即失效。
4. **回显掩码**：`GetConfig` 经 `_mask_secrets()` 把密钥值替换为 `••••`（空值保持空，不误显示已配置）。
5. **越权防护**：config 命令路由层校验 `actor.type ∈ {user, desktop}`，`agent/plugin/system` 等被 `FORBIDDEN_ACTOR` 拒绝。

### 自动化质量门禁（Phase 6 实证）
- ✅ 后端 `pytest worker/tests`：**76 passed**（含 4 个新增 config 测试）
- ✅ 前端 `tsc -p tsconfig.json --noEmit`（strict）：**exit 0**
- ✅ 后端 `ruff check` 6 个 feature 文件：**All checks passed!**
- ⏳ 三重并行 REVIEW（qa-lead 质量 / investigator 可复用性 / security-officer 安全）进行中，评分门槛 ≥ 8/10

### 关键修复记录（测试/类型驱动）
- 掩码空值误显：原 `_mask_secrets` 把空 `apiKey` 也掩成 `••••` → 改为空值保持原样，避免「未配置」误显为「已配置」。
- 覆盖层只存 `apiKey` 导致 `resolve_ai` 缺 `baseUrl`/`model` → 改为存 provider 整段（含 baseUrl/model/apiKey），DB 仍只落非密钥。
- 前端 `CommandEnvelope.commandType` union 漏加 `GetConfig`/`UpdateConfig` → types.ts 补两字面量，消除 tsc 报错。
- `useSettingsStore.deepMerge` 数组分支类型错 → 数组/基本类型整体替换，消除 `unknown[]` 类型错误。
- `App.tsx` 误用命名导入 `SettingsView`（实为 default 导出）→ 改为默认导入。
