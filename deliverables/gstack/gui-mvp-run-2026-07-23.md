# STEPWORK — GUI 在沙箱可运行 + MVP 主要功能可用（收口报告）

**日期**：2026-07-23
**场景**：调试复盘 + QA测试+发布（多成员协作，2+ 成员上场）
**参与成员**：排障手（根因诊断 + dev-bridge 蓝图）、质量门神（两轮独立 QA 验证）、主理人（汇编 + 实现 + 收口）

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟢 通过（Go）
- 阻塞项数量：0
- 环境边界（必须知悉）：本沙箱**没有 cargo/rustc、DISPLAY 为空** → Tauri 桌面二进制无法编译/显示。因此"GUI 能跑起来"在本环境落地的形态是：**React/TS 前端作为独立 Vite Web App 运行**（`npm run dev` / `npm run build`），这正是前端 `lib/tauri.ts` 既有的 standalone/mock 设计意图。
- MVP 主要功能（W1–W6：导入/转写/分析、选题角度、TipTap 脚本编辑器、渲染视图含取消/重试）**全部经 `dispatchCommand` 接通**：默认走 mock 可演示；新增 dev-bridge 后，置 `VITE_DEV_BRIDGE=1` 即走**真实 Python worker 后端**。
- 质量门神独立评分：**Health 92/100**，Go/No-Go = **Go**。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 Go（GUI 可运行 ✅；MVP 主要功能可用 ✅；仅 Tauri 原生二进制受沙箱环境所限，非代码缺陷） |
| 严重度分布 | 🔴 0 / 🟠 0 / 🟡 1（P1：AI 类命令需配 provider 密钥才有真实内容） / 🟢 若干（环境限制已知） |
| 关键行动项 | 3 条（见下） |
| 建议负责人 | 主理人（已落地）；用户提供 provider 密钥后可获真实生成内容 |

---

## 1. 各成员核心结论

### 🔧 排障手（根因诊断 + dev-bridge 蓝图）
- 核心判断：GUI"跑不起来"**不是前端问题**，根因是缺 Rust 工具链 + 无显示器，Tauri 二进制无法编译/显示；但**前端构建与 dev server 完全正常**，worker 唯一缺口是依赖 `httpx`（已补装验证）。
- 关键建议：以"前端独立 Web App + 真实后端 dev-bridge"作为沙箱内的可达目标；已给出 `worker/dev_bridge.py`（HTTP→`bus.dispatch`）+ 前端 `DEV_BRIDGE` 模式的精确蓝图，且**已实测端到端**。

### ✅ 质量门神（QA 测试与发布，两轮独立验证）
- 第一轮：构建绿（174 模块、0 TS 错误）、dev server `:1420` 返回 200 + `<div id="root">`、dev-bridge `/health` 200。主理人**当轮复验**：合法 `GenerateTopic` 信封（正确 `CommandEnvelope` 形态）经桥命中**真实** worker handler，返回 `INVALID_ARGUMENT: bad topic spec: source_version_id Field required`（真实 pydantic 校验，非 mock）；非法 `actor.type:"bogus"` 在 `parse_envelope` 层即被拒（`invalid actor.type: 'bogus' (expected one of ...)`）。8 条 MVP 命令全接通，后端单测 72 passed。Health 92/100，**Go**。
- 第二轮（独立复核）：复跑同样四项 + MVP 覆盖代码复核，结论一致 **Go**。
- 关键建议：演示真实后端交互时必须用 `VITE_DEV_BRIDGE=1` 启动 GUI；AI 类命令（选题/脚本/渲染）需 provider 密钥才有真实内容（沙箱无浏览器/DISPLAY，未做可视化级 QA，以 tsc+build+curl+e2e 替代，覆盖率有限属已知局限）。

> 主理人说明：排障手首轮返回因内部框架工具错误（`TaskStop not found in agent gstack-investigator`）一度无报告，但其真实产出随后抵达并已采信；主理人亦通过自身工具调用独立完成诊断与实现，进度为真实结论而非推测。

---

## 2. 综合审查发现（去重合并后按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源成员 |
|---|--------|------|------|---------|------|---------|
| 1 | 🟡 | 功能完整性 | `apps/desktop/src/lib/tauri.ts:174,185` | `detail` 字段被赋为 `string`，与 `CommandResult.detail: Record<string,unknown>\|null` 类型冲突，`npm run build` 报 TS2322 | 改为 `Record`（如 `{http_body: txt}` / `{cause: String(e)}`） | 主理人（构建实测） |
| 2 | 🟡 | 环境/依赖 | `worker/dev_bridge.py` | `ThreadingHTTPServer` + 共享 sqlite 连接跨线程报错；`sqlite3.Connection` 不支持事后设 `check_same_thread` 属性 | `sqlite3.connect(path, check_same_thread=False)` 构造期传入 + 请求加锁串行化 | 主理人（bridge 实测） |
| 3 | 🟡 | 功能可用性 | `worker/runtime/handlers/*`（GenerateTopic/Script/Render） | AI/ASR/TTS/Renderer 类命令需 provider 密钥；无密钥时返回 `ok:false`（如 `UNAVAILABLE`/`NOT_FOUND`），属正确真实行为 | 用户侧配置 `STEPWORK_AI_PROVIDER`/`API_KEY`/`BASE_URL` 等环境变量 | 排障手 / 质量门神 |
| 4 | 🟢 | 环境局限（已知，非缺陷） | 沙箱 | 无 cargo/rustc、DISPLAY 空 → Tauri 二进制无法编译/显示 | 真实部署需在装有 Rust 工具链 + 显示器的机器执行 `npm run tauri dev` / `tauri build` | 主理人 |

---

## ✅ 行动清单（至少 3 条具体可执行项）

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 启动真实后端桥 + GUI：`C:/Users/hexu/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m worker.dev_bridge`（后台），再 `VITE_DEV_BRIDGE=1 npm run dev` | 主理人/用户 | P0（已提供可运行证据） | 即时 |
| 2 | 配置 provider 环境变量（如 `STEPWORK_AI_PROVIDER`/`API_KEY`/`BASE_URL`）以获取真实选题/脚本/渲染内容 | 用户 | P1 | 使用真实后端前 |
| 3 | 真实机器出包：装 Rust 工具链 + 显示器后执行 `npm run tauri dev` / `tauri build`，获得原生 Tauri 窗口 | 用户 | P1 | 需原生体验时 |

---

## ⚠️ 待完善 / 已知局限

- 沙箱无浏览器/DISPLAY，未执行"点击级/console 级"可视化 QA；以 `tsc` 类型检查 + `vite build` + `curl` HTTP + 真实后端 e2e 替代，覆盖率有限（质量门神已标注）。
- 前端包体 `index.js` 约 484KB（gzip 152KB），MVP 可接受，后续可做 code-split。
- dev-bridge 使用临时/进程内 SQLite（`check_same_thread=False` + 锁串行化），仅用于本地开发/演示，**不要用于生产**。
- AI 类命令需外部 provider 密钥；无密钥时返回结构化 `ok:false`（正确真实行为，非接线问题）。

---

## 📚 成员产出索引

- 排障手（gstack-investigator）原始产出：沙箱根因（无 cargo/DISPLAY）+ `worker/dev_bridge.py` 实现蓝图（deps 构造与 `handlers/commands.py:46-54` 一致、`bus.dispatch` 签名、`parse_envelope` 契约、`lib/tauri.ts` 的 `isDevBridge` 分支、端到端 curl 实证）。
- 质量门神（gstack-qa-lead）原始产出：两轮独立 QA 报告 — ①构建绿 ②dev server `:1420` 200+root ③bridge `/health` 200 + `/dispatch` 真实 `CommandResult` ④MVP W1–W6 八命令全接通 / 后端单测 72 passed；Health 92/100，Go/No-Go = Go。
- 主理人（lead）实现落地：
  - `worker/dev_bridge.py`（新增，159 行）：HTTP 服务 `127.0.0.1:8787`，`POST /dispatch` 转发信封到真实 `bus.dispatch`，`GET /health`；CORS、`check_same_thread=False` + 锁。
  - `apps/desktop/src/lib/tauri.ts`：`DEV_BRIDGE` 模式（`VITE_DEV_BRIDGE=1` 时 `dispatchCommand`/`getWorkerHealth` 改走 bridge，含离线降级）。
  - 修复 `tauri.ts` 两处 TS2322（`detail` 类型）。
  - 已提交并推送 `eb035ff`（commit message: `feat(dev): add Python dev-bridge so browser GUI drives the real worker`）。

---

## 🧪 端到端验证证据（摘录）

```text
# 构建
npm run build → tsc -b && vite build ✅ 174 modules transformed, dist/ 产出

# GUI 作为 Web App 启动
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:1420/    → 200
curl ... :1420/ | grep '<div id="root">'                     → <div id="root">

# dev-bridge（真实后端，主理人 2026-07-21 当轮复验）
curl ... :8787/health                                          → {"status":"ok","version":"0.1.0-bridge",...}
curl -X POST :8787/dispatch  (合法 CommandEnvelope, actor.type=desktop, payload 误用 sourceVersionId)
        → {"ok":false,"error":"INVALID_ARGUMENT: bad topic spec: source_version_id Field required"}  ← 真实 handler 经桥命中（pydantic 校验，非 mock）
curl -X POST :8787/dispatch  (actor.type=bogus)
        → {"ok":false,"error":"invalid actor.type: 'bogus' (expected one of ('user','agent','plugin','system','desktop'))"}  ← 真实 parse_envelope 白名单
```

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
