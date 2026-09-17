# STEPWORK 系统化代码评审记录（十三轮）

> **本文件是这轮工作的权威详录**（评审发现、逐项修复、验证口径）。
> `COMPLETED.md §5` 与 `README.md §6` 各留一条回链指向此处，不在此复述进度表以外的内容。
> **方法论**：`yt-dev-review`（三维九域并行评审 + 分级修复 + 修复验证闭环），
> 凭据驱动 —— 每个问题都附文件/行号与"修前症状"，每项修复都配回归测试锁死行为。

---

## 0. 一页总览

| 维度 | 起点 | 终点 |
|---|---|---|
| 评审范围 | Python worker 侧为主 | Python + Tauri Rust 层对称 + 覆盖率盲区 |
| Python 测试 | 997 passed, 1 skipped | **1106 passed**（+109，其中 12 个新测试文件） |
| Rust 单测（`cargo test --lib`） | 2（仅 error.rs） | **19**（spawn/heartbeat/rpc/error） |
| 静态门禁 | `ruff` ✅ / `mypy strict` 240 files ✅ / `cargo clippy -D warnings` ✅ | 三项仍全绿，mypy 覆盖 244 files |
| 提交 | 起点 `47c825c` | 终点 `ff31612`（13 个 review commit，93 文件 +4936/−721） |

**缺陷账**：P0（正在造成生产损害）× **7**（Python 5 + Rust 2）、P1（架构/数据完整性/安全）× **19**、P2（一致性/去重/护栏）× **16**。

---

## 1. Python 侧（第 1–12 轮）

### 1.1 P0 —— 会直接损坏数据 / 打死服务

| # | 位置 | 修前症状 | 修复 |
|---|---|---|---|
| P0-1 | `handlers/backup.py` + `db/repos.py` | `Repos` 有 7 个子 repo，`_rebind_conn` 只手工重绑 5 个，漏 `video_scenes` / `hotspots` → `RestoreWorkspace` 关掉旧连接后这两个子 repo 打到 closed connection，恢复后 S2/S5 一路 `ProgrammingError` 到重启 | 抽 `Repos.rebind(conn)` 动态遍历全部子 repo 重绑；调用方零手工清单 |
| P0-2 | `logging_config.py` | ① 日志掩码关键字清单与 `handlers/config._SECRET_RE`（DB 侧剥离字段）**是两套**：`passphrase` / `credential` / 裸 `key` 在配置侧不落库、日志侧却明文进 `worker.log`，并被 diagnostics 打进**可分享的诊断包**；② `re.sub` 把 `"apiKey":"sk-…"` 整段换成 `apiKey=••••` 破坏 JSON 结构，宣称的"结构化、grep 得动"实际每条含 payload 的行都是非法 JSON | 关键字对齐 + `(?<![\w-])` 左边界防 `monkey=1` 假阳性；替换只吃 value、保留引号/分隔符/认证方案词；`MaskingFormatter` 改在 **json.dumps 之前**对明文 msg 掩码（`\"` 转义会让 `["']?` 落空） |
| P0-3 | `db/repos.py` JobRepo.update_state | 终态迁移（CANCELLED/FAILED/EXPIRED↔SUCCEEDED）无 SQL 守卫 → 用户取消渲染后 ffmpeg 工作线程 `finish_job` 把 job 复活成 SUCCEEDED 并挂上假产物 | 终态分支也带 `WHERE state NOT IN (terminal)`；迁移被拒不抛错（幂等回读当前终态） |
| P0-4 | `render/ffmpeg_runner.py` 等 | async handler 里 `subprocess.run` 无 timeout → ffprobe 损坏媒体永久挂起 = **冻结整个事件循环**（心跳停 → Rust 看门狗判死重启、CancelJob 进不来）；`scene._detect_sync` 有 to_thread 但无 timeout → 挂死线程吃光 default executor（上限 min(32,cpu+4)） | `probe_duration` 两处 +30s、场景检测 +600s；`_measure_duration`/`_concat_audio`/大文件哈希全 `to_thread` |
| P0-5 | `handlers/commands.py` + `providers/resolve.py` | 每条命令重建 6 个 Provider：Whisper 模型只在实例内部缓存，实例一换就 `WhisperModel(...)` 重载（GB 级 / 数秒）；`shutil.which`×2 + `find_spec` 每次扫盘，连 `GetConfig` 都付全价 | 进程级 `get_provider_bundle(ws_id)` 缓存，`apply_override` 作废；构建走 `to_thread` |

### 1.2 P1 —— 效率 / 数据完整性 / 安全 / 可观测性（节选高影响项）

- **共享 AsyncClient**（`net.py`）：五个 Provider 此前每请求新建 `httpx.AsyncClient` = N 次 TLS 握手 + N 次代理探测（探测本身阻塞事件循环 0.25s）。改 `shared_async_client()` 进程级单例，Provider 只借不关，`runtime.shutdown` 里 `aclose_shared_async_client` 统一释放；代理探测加 5 分钟 TTL 缓存。**顺带抓出** `tts/cloud.py` 与 `image/openai_compatible.py` 的 `async with (self._client or new())` 会把注入的共享 client 首次调用后就关掉。
- **MCP 子进程互锁**（`agents/mcp_client.py`）：`stderr=PIPE` 无并发 reader，Server 写满 OS 管道 → write 阻塞 → 主协程等 stdout 响应也阻塞到 30s 超时。改后台泵 + 128KB 环形缓冲。
- **逐幕并发**（`illustrate_scenes` / `synthesize_scenes`）：8 幕串行 await 外部 API = N×RTT。改 `Semaphore(4)+gather`；配音只并发「合成+测时长」，`start_sec` gather 后按 seq 顺序累加（时间轴语义不变）。
- **`project_io` 事务 + zip 形状**：4 段 INSERT 未包 `with conn:` → 中途失败留半个项目 + 悬着的隐式事务被下条命令提交；bundle 成员零形状校验（缺 project.json 崩 KeyError、versions 不是 list 崩 TypeError）、zip bomb 无上限（整段 `read()` 进内存）。改事务包裹 + `_read_bundle_json` 形状校验 + 单成员 2GB 上限 + 64KB 分块 copy + 媒体 IO 走 `to_thread`。
- **`import_source` 静默删素材**：`content_hash` 只判 truthy，传 int/dict 也能入库，误命中别人 hash → dedup 分支 `os.remove` 把刚下载完的素材静默删掉。加 `isinstance(str)` 守卫。
- **publish 授权 fail-closed**：`_authorization_error` 里 payload 损坏/缺 content_hash 时 `if expected and …` 短路静默放行 → 篡改载荷绕过授权。改成拿不到 expected 也拒绝。
- **幂等原子化**：`lookup→执行→remember` 三步非原子，两条同 key 命令并发都 miss 都执行都计费。改三阶段 `reserve`（INSERT OR IGNORE 写 INFLIGHT 哨兵抢席位）→execute→remember/release，bus 三条失败退出都 release。三张长期只增的表（idempotency/metrics/audit）接入 `db_retention_sweep`。
- **跨 workspace 审计/审批泄露**（迁移 0015）：`ListAuditEvents` / `ListApprovalRequests` 无 workspace 过滤、`audit_events` 根本没这列，而二者在 Agent 允许清单里 → 外部 MCP Agent 能读全库。加 `workspace_id` 列 + 写侧带上 + 读侧 `WHERE workspace_id=?`（NULL 老行宁少勿多）。
- **可观测性**：`bus.dispatch` 兜底 `except Exception` 从不记日志 → handler 崩溃 `worker.log` 零 traceback。补 `logger.exception`；`logging_config` 顺带修 §1.1 P0-2 的 JSON 碎裂。
- **审批决策审计真实性**：决策落点 `actor.get('type', 'user')` → 上游忘传字段时高风险操作被记成"人批的"（审计造假）。改 `'unknown'`。

### 1.3 P2 —— 架构债 / 去重 / 一致性（多为"结构性一次到位"）

- `DispatchError` 真身从 `commands/bus.py` 搬到 `runtime/errors.py`，bus re-export 保 43 处老 import；生产代码反向依赖归零（`test_batch8` AST 扫描兜底）。
- Provider 分发**表驱动**：`resolve.py` 五类（ASR/AI/TTS/Renderer/Publish）的 `if kind==…` 链 → 五张 `*_FACTORIES`；hint 路径也接进来（`AI_HINT_FACTORIES`），env/hint 共享 `_ai_build_*` 纯构造。`test_provider_registry` AST 锁"分发器里不再出现 if kind == 字符串"。
- 双组合根合一：`app.run_command`（CLI/MCP）与 `handlers/commands.handle_command`（Rust sidecar）此前装配漂移（前者绕开缓存、漏注 notify/worker_state）→ 抽 `deps.build_deps(state, ws_id)` 单一入口；`test_build_deps` AST 扫描生产代码禁止裸 `Deps(...)`。
- `Deps` 9/11 `Any` → 领域 Protocol 标注，`TYPE_CHECKING` 前向引用避免循环。
- 校验去重：`validation.require_positive_int` + `parse_spec` 收口 5 处 limit 校验 + 4 处 pydantic 转译；`_resolve_stepwork_home` 5 处私有副本 → `cleanup.resolve_stepwork_home` 单一入口。
- 跨语言一致性护栏：`MAX_FRAME_SIZE` Python/Rust 各一份 → `test_protocol_parity` 读 Rust 源码 eval 比对。
- `Optional[X]` 与 `X | None` 在 `db/repos.py` 混用 → 统一后者。

---

## 2. Rust 侧（第 13 轮 · 对称评审）

基线 `cargo check` / `clippy -D warnings` 全绿，但两个 P0 全在并发/UTF-8 边界、整个 Rust 层仅 2 个测试。

| # | 位置 | 修前症状 | 修复 |
|---|---|---|---|
| P0-R1 | `sidecar/spawn.rs` | stderr ring buffer `buf.drain(..len-N)` 落在 3 字节汉字中间 → `String::drain` **panic**（byte index not char boundary），诊断信息全丢 + ring 半新半旧 | `trim_to_char_boundary` 向前回退到合法边界；4 单测（含"经"×10 让边界正好切中间） |
| P0-R2 | `sidecar/heartbeat.rs` + `lib.rs` | 心跳超时后每 1s tick 都 send restart；monitor 处理一条 restart（kill→spawn→ready 最长 10s）期间 channel 堆积 → **restart storm**；且 lib.rs 直接改 last 不 reset 会让 watchdog 首次 fire 后永不复醒 | 边沿触发（`AtomicBool` + `compare_exchange`），`HeartbeatHandle.record()` 一次完成"刷新+复位"；3 个 tokio 单测 |
| P1-R3 | `sidecar/spawn.rs` | ready 握手不 `try_wait()` → Python 一 spawn 就崩也白等满 10s，用户视角"点了没反应" | 每轮失败补 `child.try_wait()` 早退 |
| P1-R4 | `sidecar/rpc_client.rs` | 读侧 `as_str()` 只认 string id，未来 echo 数字 id 会静默 miss pending 只等超时 | `extract_id` 归一化 number→string；端到端单测 |
| P1-R5 | `error.rs` | `format!("{:?}").to_uppercase()` 把 `SpawnFailed`→`SPAWNFAILED`，与 serde 序列化的 `spawn_failed` 漂移 | `as_code()` 显式 SCREAMING_SNAKE 映射 + 穷举 match（漏配编译不过） |

---

## 3. 覆盖率盲区（第 11.5 轮 · `pytest --cov`）

跑覆盖率区分"真盲区"与"可选依赖装不上的假盲区"：whisper/edge/playwright 40–64% 是 optional extras（CI 不装），非缺陷。补两处**每单都会走却零覆盖**的主流程：`ingest/metadata._probe`（36%→~90%，ffprobe 成功路径/无 format.duration 兜底/N-A/非零退出/坏 JSON 五条）+ `hotspot/mcp.resolve_connection` 显式 id 的三条错误分支。

---

## 4. 刻意**不做**的（性价比裁决）

- **大文件 sha256 → 进程池**：`_video_content_hash` 已流式化 + `to_thread`；CPython `hashlib` 对 >2KB chunk 已释放 GIL，换进程池要处理 SQLite 连接不能跨进程，收益≈0。
- **继续卷覆盖率到 95%+**：93% 已高，剩余多为防御性 except / `__main__` 分支，"为指标而测"反而稀释信号。
- **Agent 三套客户端强行抽一个 `AgentTransport` 抽象**：MCP(stdio 短连)/A2A(HTTP)/ACP(stdio 长连双向)传输差异是本质，硬抽会做出谁都不合身的抽象；改为只共享"确实相同"的部分（channel 常量 + 信任/复核留痕）。

---

## 5. 复现与验证口径

```bash
# Python
cd /d/Code/StepWork
python -m ruff check worker/
python -m mypy worker/
python -m pytest worker/tests/ -q --cov=worker --cov-branch   # 1106 passed, 1 skipped, 93%
# Rust
cd apps/desktop/src-tauri
cargo check && cargo clippy --all-targets -- -D warnings && cargo test --lib   # 19 passed
```

新增 12 个测试文件：`test_p0_regressions` / `test_p1_regressions` / `test_p1_batch2` / `test_p2_batch3` / `test_batch4` / `test_batch8` / `test_build_deps` / `test_provider_registry` / `test_channel_timeouts` / `test_workspace_scope` / `test_protocol_parity` / `test_coverage_blindspots`。

commit 范围 `47c825c..ff31612`（13 个 review commit）。
