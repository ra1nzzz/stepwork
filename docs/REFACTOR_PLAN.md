# STEPWORK 优化计划（工程视角，不受 PRD/原型约束）

日期：2026-07-27 · 基线：630 后端测试 + 26 前端测试全绿，mypy/ruff 干净

这份计划不问「PRD 要什么」，只问一件事：**这套代码再长两倍时，哪里会先塌。**

---

## 现状体检

| 维度 | 数字 | 判断 |
|---|---:|---|
| 后端（worker/cli/mcp） | 18,194 行 | 结构清晰，命令总线是好架构 |
| 前端（apps/desktop/src） | 12,747 行 | **缺数据层与设计系统** |
| 后端测试 | 14,246 行 / 630 例 | 扎实 |
| 前端测试 | 26 例 | **与 12.7k 行严重不匹配** |
| Rust sidecar | 1,467 行 / 9 例 | 规模合适 |
| 共享 UI 组件 | 7 个 | 太少 |
| 内联 `style={{}}` | 154 处 | 设计系统缺位 |
| 手写 dispatch+loading+error | 79 处 | 应由数据层承担 |
| 其中**未检查 `res.ok`** | 10 处 | **静默失败** |
| 前端裸类型断言消费 `detail` | 46 处 | **跨边界无保障** |
| 装了从未使用的依赖 | 4 个 | 见下 |
| 可观测性 | 38 处 log / 0 指标 | 近乎为零 |
| 迁移回滚脚本 | 0 / 10 | 升级失败无退路 |

---

## 一、根因：命令**响应**没有契约（P0）

这是我认为**唯一真正的架构缺陷**，其余多数问题都是它的症状。

请求方向做得很好：`command-envelope.schema.json` 是单一事实源，`test_command_registry`
锁住 bus / schema / types.ts 三处一致，加错命令会立刻红。

响应方向**完全裸奔**：

```python
detail: dict[str, Any] = Field(default_factory=dict)   # 后端
```
```ts
const d = (res.detail ?? {}) as { connections?: AgentConnection[] };  // 前端，46 处
```

后端把 `detail.dedup` 改名成 `detail.deduplicated`，前端拿到 `undefined`，
**没有任何编译错误、没有任何测试变红**，去重提示就此静默消失。

这不是假想。本次会话内我亲手撞了两次：

- `source_assets.media_meta` —— 实际列名是 `metadata`，运行时才炸；
- transcript 的 `content` 我当 JSON 解析，实际是**纯文本**（分段在 `producer.segments`），
  `json.loads` 失败被 `except` 吞掉，**剪辑时间线的 marker 永远为空**，而我自己的
  测试恰好按同样的错误假设造数据，于是全绿。

> 测的是假设，不是系统。没有契约时，测试会和实现一起错。

### 方案

为每条命令的 `detail` 定义 schema，从单一来源生成两端类型。

1. `schemas/results/<CommandType>.schema.json`，或更省事：在 worker 侧用 Pydantic
   定义每条命令的 result 模型（已有 pydantic 依赖），由它导出 JSON Schema；
2. 生成 `apps/desktop/src/lib/results.generated.ts`，前端 `dispatchCommand<T>()`
   按 commandType 推导 detail 类型，删掉 46 处断言；
3. 扩展 `test_command_registry`：**每条 bus 路由都必须有 result schema**，
   缺了就红 —— 与命令注册同一条不变量；
4. CI 加一步「生成物与源同步」检查（同 `analysis.schema.json` 已有的做法）。

**收益**：字段改名从「线上静默失效」降级为「编译不过」。这是本计划里投入产出比最高的一项。

**代价**：约 60~80 条命令，一次性投入不小。建议按域分批（先 publish/agent 两个改动最频繁的域）。

---

## 二、前端缺数据层（P0）

### 2.1 react-query 装了从来没用过

```
@tanstack/react-query   已安装，src 中零引用
@tiptap/react|pm|starter-kit  已安装，src 中零引用
```

与此同时，79 处手写「dispatch → setLoading → try/catch → setError → setData」。
这是**已经买了工具却在徒手搬砖**。

TipTap 那三个包大概是为段落级编辑装的，但该功能只做到了后端命令，富文本编辑器从未落地
——要么补上，要么卸载，不要留着假装有。

### 2.2 10 处 dispatch 静默失败

```
features/agent/AgentView.tsx:137,150   setConnStatus / deleteConn
features/publish/PublishView.tsx:194,246  cancelSchedule / 其它
stores/useImportStore.ts:227
stores/useRenderStore.ts:203
stores/useTranscriptStore.ts:123
```

失败后 UI 照常刷新、显示旧状态、一句提示都没有。用户点「停用连接」，
后端拒了，界面上看不出任何异常 —— 这是最难排查的一类缺陷。

其中 AgentView / PublishView 那几处是**我这两批加的**，应当补上。

### 方案

引入一层 `useCommand` / `useCommandMutation`（基于既有的 react-query）：

```ts
const { data, isPending, error } = useCommand("ListAgentConnections", { });
const setStatus = useCommandMutation("SetAgentConnectionStatus");
```

- 统一 loading / error / 空态，`res.ok === false` 一律抛错进 error 通道，
  **不给静默失败留出口**；
- 配合第一项的生成类型，`data` 直接是强类型；
- 顺带拿到缓存与失效（当前每个视图都在手动 `loadXxx()` 重拉）。

预计能砍掉 400~600 行样板，并从结构上消灭「忘了检查 ok」这个类别。

---

## 三、Agent 出站三件套的重复（P1）

`mcp_client.py` / `a2a.py` / `acp.py` 各自实现了一遍几乎相同的东西：

- `_record_call` / `_record_prompt`：写 `agent_tasks` + `agent_artifacts`
  （`agent_record.py` 里还有第四份）
- `_load_connection`：查连接 + 校验 protocol + 校验 status
- `_row_to_dict`（全仓 **10 份**）、`_now()`（**8 份**）
- `TRUST_LEVEL` / `REVIEW_STATE` 常量各 **4 份**

这是我自己在三批里连写三遍留下的。第四个协议进来时会变成第五份。

### 方案

抽 `worker/runtime/agents/registry.py`：

```python
class OutboundChannel:            # 一个出站协议通道的共同行为
    protocol: str
    def load(self, deps, conn_id) -> dict          # 查+校验
    def record(self, deps, env, *, task_type, text, ok) -> str   # 统一留痕
```

三个 handler 只保留各自的传输差异（stdio 短连接 / HTTP / stdio 长连接双向）。
同时把 `_row_to_dict` / `_now` 收进 `worker/runtime/db/rows.py` 与 `utils/time.py`。

**注意**：这项要在第一项（响应契约）之后做，否则重构会顺手改 detail 形状而无保护网。

---

## 四、巨型文件拆分（P1）

| 文件 | 行数 | 问题 |
|---|---:|---|
| `SettingsView.tsx` | 1,430 | 6 个 Tab，部分抽了 Panel、部分内联 —— 半拉子状态最难维护 |
| `CreateAnalysisView.tsx` | 858 | |
| `cli/__main__.py` | 847 | 所有子命令挤在一个文件 |
| `handlers/publish.py` | 627 | **一个 handler 11 个 commandType 分支** |
| `lib/types.ts` | 811 | 会被第一项的生成类型自然拆解 |

`publish.py` 尤其值得动：11 个分支的 `if/elif` 长链，新增命令只能往里塞。
按子域拆成 `publish/variants.py` / `publish/authorization.py` / `publish/schedule.py`
（schedule 已经是独立模块，做法可复制）。

`SettingsView` 应把 6 个 Tab 全部抽成独立 Panel 组件，一个都不留在主文件里 ——
半抽半留比全部内联更糟，因为读代码的人不知道该去哪找。

---

## 五、设计系统（P1）

154 处内联 `style={{}}`、7 个共享组件、1,372 行 CSS 里只有 43 行是 token。

这意味着间距/圆角/色值散落各处，改一次视觉要全仓翻。建议：

1. 扩充 `tokens.css`：间距阶、圆角、层级、动效时长；
2. 抽出真正复用的原子件：`Panel` / `FormRow` / `StatusBadge` / `EmptyState` /
   `InlineActions` / `NoticeBar`（这几个在各视图里已被手抄多遍）；
3. 定一条规矩：**内联 style 仅允许用于动态计算值**（如进度条宽度），其余进 CSS。

---

## 六、可观测性（P2）

18k 行后端只有 38 处日志、0 个指标。出问题时基本靠猜。

- 给 `dispatch` 加统一的结构化日志：commandType / 耗时 / ok / error code / correlationId；
- `correlationId` 贯穿 UI → Rust → worker → provider 调用，诊断包里能串起完整一次操作；
- 落一份轻量本地指标（命令计数、P50/P95 耗时、失败率），写进 SQLite 即可，
  不必引 Prometheus —— 本地优先的产品不该为此拉起一套监控栈；
- 诊断包（已有 `ExportDiagnosticsBundle`）带上这些。

---

## 七、迁移无回滚（P2）

10 个迁移、0 个回滚脚本。一旦某次升级中途失败，用户的库停在半路，只能靠备份。

- 每个迁移配 `NNNN_xxx.down.sql`；
- 升级前自动快照（`BackupWorkspace` 已有能力，接上即可）；
- 加一个「迁移到最新再回滚到初始」的往返测试，防止 down 脚本写了但不能用。

---

## 八、前后端测试失衡（P2）

630 : 26。前端 12.7k 行几乎裸奔。

不建议追求覆盖率数字，建议**只测三类**：

1. 跨边界契约（第一项落地后自动获得）；
2. store 的状态迁移（已开了头：settings 密钥不落盘、import 去重提示）；
3. 关键路径的冒烟（导入→转写→分析→脚本→渲染→发布，用 mock 的 dispatch 走一遍）。

Rust 侧 9 个测试对 1,467 行尚可，但 sidecar 崩溃/重启路径值得再补两例。

---

## 九、清理（P3）

- 卸载 `@tanstack/react-query`（若不采纳第二项）或**用起来**（推荐）；
- TipTap 三件套：补上富文本编辑器，或卸载；
- `agent-interop/` 五个空 `.gitkeep` 目录已无意义（实现都在 `worker/runtime/agents/`），删掉；
- `ListAgentConnections` 的 N+1 查询（每条连接一次 `COUNT(*)`）改成一次 `GROUP BY`；
  当前连接数少不影响，但这是会随数据增长而恶化的写法。

---

## 排期建议

分三个批次，每批可独立交付、独立回滚：

**批次一（地基，约占总量 40%）**
1. 响应契约 + 类型生成（先 publish / agent 两域）
2. `useCommand` 数据层 + 修掉 10 处静默失败
3. 卸载或启用死依赖

> 做完这批，「改后端字段前端静默失效」这个类别就消失了。后续所有重构都在保护网内进行。

**批次二（收敛，约 35%）**
4. Agent 出站三件套抽公共层
5. `publish.py` 与 `SettingsView.tsx` 拆分
6. 设计系统 tokens + 6 个原子件

**批次三（运维，约 25%）**
7. 结构化日志 + correlationId + 本地指标
8. 迁移回滚 + 往返测试
9. 前端契约/store/冒烟三类测试补齐

---

## 我明确**不**建议做的

- **换状态管理**：zustand 用得挺好，问题在缺数据层不在选型；
- **上 Prometheus/OTel**：本地优先的桌面产品，SQLite 里存指标足够；
- **追前端覆盖率数字**：三类关键测试的价值远高于把覆盖率刷到 80%；
- **拆微服务/多进程**：当前单 worker + sidecar 的边界是对的，没有理由复杂化；
- **重写 CLI**：847 行虽长但结构直白，拆分优先级低于上面所有项。

---

## 执行结果（2026-07-27）

三批全部完成，逐项对照：

| 项 | 状态 | 实测变化 |
|---|---|---|
| 1 响应契约 + 类型生成 | ✅ | 32 条命令建模；dispatch 出口真校验；生成 TS + JSON Schema，CI 校验同步 |
| 2 useCommand 数据层 | ✅ | 10 处静默失败全修；react-query 从「装了没用」变成真在用 |
| 3 Agent 三件套抽公共层 | ✅ | mcp_client 290→183 行；TRUST_LEVEL 等常量 4→1 份 |
| 4 巨型文件拆分 | ✅ | publish.py 627→53 行路由 + 3 子域；SettingsView 1430→231 + 6 Panel |
| 5 设计系统 | ✅ | tokens 补齐；6 个原子件；内联 style 154→47 |
| 6 可观测性 | ✅ | 结构化日志 + correlationId + SQLite 指标，进诊断包 |
| 7 迁移回滚 | ✅ | 11 个 down 脚本 + 一步一停回滚 + 真往返测试 |
| 8 前端测试 | ✅ | 26 → 54 例（契约 / store / 冒烟三类） |
| 9 清理 | ✅ | 4 个死依赖处理完；空目录删除；N+1 改 GROUP BY |

**基线变化**：后端 630 → 675 例，前端 26 → 54 例，mypy/ruff 全程 0。

### 执行中查出的真问题（计划里没预料到的）

契约与测试上线当天就开始干活，这几个都是**改造前完全不会被发现**的：

1. **A2A Server 协议 bug**：鉴权失败时不读请求体就回 401 并关连接，客户端还在
   写 body → 收到连接重置而非干净的 401。此前一直表现为「测试偶发红」，
   我曾误判为 Windows socket 抖动。任何带 body 的未授权请求都会撞上。
2. **§11.3 掩码对 JSON 无效**：正则要求关键字紧跟 `:`，而 JSON 是
   `"apiKey": "sk-..."` —— 所有 JSON 形式的密钥都掩不掉，而结构化日志全是 JSON。
3. **down 脚本会被当 up 执行**：`0001_init.down.sql` 字典序排在
   `0001_init.sql` 之前，全新库上会导致建表脚本被永久跳过（我自己引入，
   当天由新增的往返测试抓出）。
4. **契约与实现不符 11 处**：写模型时按想象而非按实际写，dispatch 校验当场
   全部拦下（`ExportBundle.files` 实际是 dict 不是 list 等）。
5. **`ExportEditTimeline` 取不到 marker**：transcript 的分段在 `producer` 而非
   `content`，此前 json.loads 失败被 except 吞掉，而我的测试恰好按同样的错误
   假设造数据 —— 测的是假设不是系统。

第 5 条最能说明契约的价值：**没有契约时，测试会和实现一起错。**
