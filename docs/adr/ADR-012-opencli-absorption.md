# ADR-012: 吸纳 OpenCLI 的边界 —— 消费，不内嵌

- **Status**: Accepted
- **Date**: 2026-09-12
- **Deciders**: @ra1nzzz

## Context

[OpenCLI](https://github.com/jackwener/opencli)（`@jackwener/opencli` v1.8.8，1592 commits）把「复用本机
Chrome 登录态」做成 100+ 站点的**确定性 CLI 命令**，另可经 CDP 驱动 Electron 桌面应用。
与 STEPWORK 的交集集中在三处：

| 交集 | OpenCLI 已有能力 | STEPWORK 侧现状 |
|---|---|---|
| **S7 发布引擎** | `douyin`（13 命令，含 `publish` / `draft` / `drafts`）、`xiaohongshu`（含 `publish`）、`weibo`（含 `publish`）、`bilibili` | `publisher-engine/` 是**全空壳**（5 个子目录仅 `.gitkeep`） |
| **S5 热点源** | `weibo hot` / `zhihu hot` / `bilibili hot` / `xiaohongshu feed`（均 🔐 Browser 模式，带登录态） | 已有 11 个免登录源（含 NewsNow 聚合），实测 0 errors |
| **Agent 互操作** | `opencli external register <name>` —— 把任意本地 CLI 挂进统一发现面，**stdio 与退出码透传** | `stepwork-cli` 无对外注册入口 |

**许可证**：`Apache-2.0`（已核 `package.json`；运行时依赖 `commander`/`js-yaml`/`undici`/`ws`/`turndown` 等
全为 MIT / Apache-2.0 系）。与 ADR-006 的双许可（Core = **AGPL-3.0-or-later**）**兼容**，
引入需在 `THIRD_PARTY_NOTICES.md` 登记 —— 这与 `douyin-live-info` 的 **CC BY-NC 4.0**
（只能借鉴原理）是两种完全不同的处境。

**硬约束**：ADR-008 —— V0.1–V0.5 **只允许 FILL_AND_PREVIEW**，「最终点击发布按钮必须由用户手动完成」，
且显式禁止反检测脚本、多账号轮换规避风控。

## Decision

**把 OpenCLI 当「可选的外部能力源」消费，不把它写进依赖树，也不内嵌其代码。**

1. **S7 采纳为默认底座候选，但只走 fill / draft 路径**
   - ✅ 可用：`douyin draft` / `douyin drafts` / `weixin create-draft` —— 与 ADR-008 的
     FILL_AND_PREVIEW 天然对齐（填完存草稿，人再点发布）
   - ⛔ **禁用**：`douyin publish` / `xiaohongshu publish` / `weibo publish` ——
     自动点发布直接违反 ADR-008
   - 接入形态照 S2 的 `ImageProvider` 先例：**接口先于实现**。列 `[publish-opencli]` 可选依赖；
     未安装 / daemon 未起 / 未登录一律显式 `UNAVAILABLE` 或 `NEED_LOGIN` 报错，**不静默降级**。
     （`opencli doctor` 与它的 `sysexits.h` 退出码正好够做这个三态判别。）
2. **S5 暂不采纳**：热点链路已 11 源 0 errors，且其立身之本是「独立 MCP Server + 零运行时依赖」（P2）。
   为拿更实时的热榜去引入 Node≥20 + Chrome 扩展 + 常驻 daemon，与那条原则冲突，收益不成比例。
   记为**备选路径**：当某个源长期失效、或需要登录态深度数据（如抖音热点宝的 200+ 垂类）时才重新评估。
3. **反向注册，零成本试水**：`opencli external register stepwork --binary stepwork-cli`，
   把 STEPWORK CLI 挂进它的统一发现面，写进文档供用户自行启用（不写代码）。
4. **借鉴四条设计**（只借鉴，不引代码）：
   - 退出码沿用 `sysexits.h` 语义表达**可操作状态**（`66` 空结果 / `69` 桥断开 /
     `75` 超时 / `77` 需认证 / `78` 配置错），与 STEPWORK「错误要教人怎么修」一致
   - 适配器编写法 `recon`（侦察站点模式）→ `init` → `verify`（含行形状校验）
   - 站点知识持久化到 `~/.opencli/sites/<site>/`（把「站点怎么抓」当一等资产）
   - 行形状约束（≤12 顶层键、`id` 类字段必须在顶层）与网络捕获脱敏

## Consequences

**正面**：
- S7 从「四个平台各写一套浏览器自动化」变成「接一个 Provider」，工作量差一个量级
- 许可证干净（Apache-2.0 → AGPL 兼容），可放心做深度集成而非停留在「借鉴原理」
- 双向互操作几乎零成本
- 上游维护活跃（有 `autofix` 机制与站点文档），比自研更抗站点改版

**负面 / 风险**：
- **重依赖**：Node ≥ 20.18.1 + 全局 npm 包 + Chrome 扩展 + 常驻 daemon（`127.0.0.1:19825`）。
  桌面端要多装三样，且多一个常开本机进程 —— 必须**可选**，并在 UI 讲清「不装就少一个能力」
- **适配器脆弱**：站点改版即失效（上游自己都要 `opencli-autofix` 修）。STEPWORK 侧必须把
  「发布失败」定义为**明确降级 + 人工兜底**，绝不静默失败
- **无 MCP server**：OpenCLI 走 Skills 路线（`npx skills add jackwener/opencli`）而非 MCP。
  接入只能 subprocess（照 `McpStdioClient` 的进程纪律：不继承 stdin、超时强杀、退出码+stderr 一并回显），
  或在 MCP 层自建包装
- **登录态复用 = 外部动作**：发布/发帖类必须走既有 Approval Center 与
  `publish_authorization`，不得绕过（SOUL：外部动作要谨慎）

**关联**：ADR-006（双许可）、ADR-008（发布自动化边界）、`docs/ROADMAP.md` S7 / S5、REFERENCE.md §4

## 2026-09-13 落地补充（装完之后，全是真机事实）

**已装**：`npm i -g @jackwener/opencli` → **v1.8.7**（上文记的 v1.8.8 在 npm 上不存在，
最新即 1.8.7；许可证 Apache-2.0 ✅ 与 ADR 一致）。只装了工具，**没装浏览器扩展** ——
所以「填表」这条路当前不可达，只能探到达不到。

**已落地**：`worker/runtime/providers/publish/{base,opencli}.py` —— 只有 `probe`，
没有任何 `publish` 路径。判据从 ADR 设想的 `doctor` 换成
`auth status --site <site> --format json`。

### 三条真机修正（其中一条推翻了本 ADR 的「借鉴」项）

1. **「退出码沿用 `sysexits.h` 语义表达可操作状态」这条被证伪** —— 它在**文档里**
   成立、在**实现里**不成立：`dist/src/doctor.js` 里根本没有 `process.exit`，
   实测「扩展未连接」时它打印 `[FAIL] Connectivity` 却照样 `exit 0`；
   `auth status` 在 `status="error"` 时也 `exit 0`。
   ⇒ 上文「借鉴四条设计」的第 1 条要打折：**只借鉴语法，不指望语义**。
   我们侧把语义收窄成「非 0 一定不可用，`0` 什么都不证明」（`base.classify_exit`）。
2. **Windows 上 npm 全局命令是 `.cmd`**：`CreateProcess` 只自动补 `.exe`，
   用裸名 spawn 必然 `FileNotFoundError [WinError 2]`；必须 `cmd /c <path>` 包装。
3. **它的连接超时是 45s，且这个数是可覆盖的**：扩展没连时 `auth status` 会等满
   `DEFAULT_BROWSER_CONNECT_TIMEOUT`（`dist/src/browser/config.js`，默认 **45**，
   可由 `OPENCLI_BROWSER_CONNECT_TIMEOUT` 覆盖），实测 **t+46.1s 才写出第一个字节**、
   紧接着退出、`exit 0`。我们主动把这个变量压到 8s（实测全程 9.3s），
   自己的保险丝设 35s。

   ⚠️ 记一条**我自己的误判**供后来者避坑：我先把它读成「输出完不退出 + stdout 非
   tty 全缓冲」，并据此写了「轮询到完整 JSON 就 kill」的收工信号。对照实验逐条证伪：
   `--version` 经同一管道 0.6s 返回 6 字节且正常退出；重定向到文件同样为空
   （因为那时它还没写）。**真因只是慢**，而我的 25s 上限在它写第一个字节前就动刀。
   ⇒ 结论：**给外部工具的等待上限，必须大于它自己的上限**，否则你测到的是
   自己的耐心，不是它的状态。

### `fill` 已侦察、**刻意未落地**

真实形状已抓到（`opencli browser --help` / `browser <session> fill --help`）：

```
opencli browser <session> fill [options] [targetOrText] [text]
  --role/--name/--label/--text/--testid/--nth/--tab
→ JSON: { filled, verified, text, actual }
```

**不写进 `PublishProvider`**，理由是本 ADR 与 `base.py` 已经在讲的那一条：
契约照**已核实的事实**写。这份形状只来自 `--help` 与文档，**没在真机上填过一次**
（扩展没装，跑不通）。照它写死签名，后来者会照着一个没验证过的契约去实现 ——
而这一轮我们刚刚为「照文档假设」付过一次学费（第 1、3 条）。
等扩展连上、真填一个表单之后再按 `probe` 的同一范式补，并同步更新
`test_protocol_exposes_only_name_and_probe`（那条断言是**变更检测器**，
它红是应该的，红的时候请走 ADR-008 的边界核对，别直接放宽它）。

**验收物**：`.workbuddy/publish-acceptance/`（不进 git）——
`probe_opencli_io.py`（逐项证伪三条误判）、`probe_opencli_timeline.py`（首字节 vs 退出的时间轴）、
`probe_real_provider.py`（真 Provider 探真工具，9.3s 得到 `BROWSER_CONNECT`）。
