# STEPWORK 性能基线（W9 L.42）

> **状态**：RC1 基线（2026-07-24 建立）
> **测试源**：`worker/tests/test_performance.py`（`@pytest.mark.perf`，默认不跑）
> **建立命令**：`python -m pytest worker/tests/test_performance.py -m perf -s`

---

## 1. 测量方法

- **测量方式**：`time.perf_counter()` 包裹 `dispatch()` 调用，记录端到端耗时（毫秒）。
- **稳态采样**：每次独立 setup（新 in_memory DB + 全套 fake providers），
  丢弃前 2 次 warmup，后续 5 次取**中位数**（降噪）。
- **路径**：进程内 `dispatch`（非真实 worker 子进程），复用 fake providers
  （`LocalASRProvider` / `_FakeAI` / `LocalTTSProvider` / `fake_ffmpeg.py`），
  避免 CI/不同机器外部依赖抖动。
- **限制**：fake providers 路径**只反映框架开销**（DB + dispatch + job 状态机 +
  序列化），不包含真实 AI/ASR/TTS/ffmpeg 耗时。真实 provider 基线需在接入后
  单独建立并回填到本文件 §4。

---

## 2. 测试机器

| 项 | 值 |
|---|---|
| OS | Windows 11 (10.0.22631) |
| Python | 3.13.14 |
| CPU | （开发机，记录时填入） |
| RAM | （开发机，记录时填入） |
| 磁盘 | NVMe SSD |
| pytest | 9.1.1 |
| 说明 | 本基线在开发机建立；CI/其他机器数值会有差异，允许区间见 §3 |

---

## 3. 基线数值（fake providers 路径）

| 命令 | 中位数 (ms) | 阈值 (ms) | n | 备注 |
|---|---|---|---|---|
| `ImportSource` | 0.96 | 50 | 5 | 仅 DB insert + hash |
| `TranscribeSource` | 1.38 | 80 | 5 | 含 LocalASRProvider（确定性 fixture） |
| `AnalyzeSource` | 1.41 | 80 | 5 | 含 _FakeAI.complete（即时返回） |
| `GetProvenance` | 0.74 | 30 | 5 | 回退 producer 路径（无 provenance_records） |
| `ExportProject` | 3.14 | 100 | 5 | 含 zip 打包（2 条 version） |
| `FullPipelineColdStart` | 63.40 | 800 | 5 | 7 条命令串行（Import→Render），含 migrations |

**说明**：

- 阈值（threshold）是宽松上限，超过才视为回归。基线值远低于阈值，留有充足余量。
- `FullPipelineColdStart` 包含 migrations（建表）+ provider 构造 + 7 条命令串行，
  是单次冷启动的完整画像。
- 单命令稳态值之和（~8 ms）远小于全链路冷启动（63 ms），差值来自 migrations
  和 provider 构造的一次性开销。

---

## 4. 真实 provider 基线（待补）

> 接入真实 ASR/AI/TTS/ffmpeg 后，在此处记录真实路径基线。
> 建议格式同 §3，并标注 provider 名称、模型、网络环境。

| 命令 | Provider | 中位数 (ms) | 阈值 (ms) | 备注 |
|---|---|---|---|---|
| `TranscribeSource` | （待补） | — | — | 真实 ASR |
| `AnalyzeSource` | （待补） | — | — | 真实 AI |
| `GenerateTopic` | （待补） | — | — | 真实 AI |
| `GenerateScript` | （待补） | — | — | 真实 AI |
| `CreateRenderJob` | （待补） | — | — | 真实 ffmpeg |

---

## 5. 回归判定

- **默认不跑**：`@pytest.mark.perf` 标记，CI 走 `pytest -m "not perf"` 跳过
  （避免 CI 抖动引入噪声）。
- **手动建立**：发布前或重大重构后，手动 `pytest -m perf -s` 跑一遍，将中位数
  回填到 §3（更新「中位数」列，不调阈值）。
- **回归阈值**：中位数超过 §3 的「阈值」列即判回归，需调查原因（DB 锁、
  N+1 查询、序列化膨胀等）。
- **阈值调整**：仅当架构变更（如引入真实 provider）才调整阈值，并在 PR 中
  说明理由。fake providers 路径的阈值保持宽松，不作为发布门禁。

---

## 6. 已知限制（W9_PLAN R6）

- **机器差异**：不同机器数值差异大，本基线不阻塞 Gate；只用于趋势对比。
- **fake providers 不代表真实负载**：真实 AI/ASR 耗时主导时，框架开销可忽略；
  本基线仅用于发现**框架层面的回归**（如 dispatch 路径变慢、DB 索引丢失）。
- **未覆盖并发**：当前只测单命令串行；并发压测推 V0.2。
- **未覆盖大数据量**：当前 version 数 ≤ 5；万级 version 的查询性能推 V0.2
  （届时需加索引和分页基线）。

---

## 7. 升级到 pytest-benchmark（V0.2）

当前用 `time.perf_counter` + 手动中位数，足以建立数量级基线。V0.2 升级到
`pytest-benchmark` 后可获得：

- 自动统计（min/max/mean/stddev/median）。
- 回归对比（`--benchmark-compare`）。
- 历史趋势图。

迁移时保留 `@pytest.mark.perf` 标记和本文件结构，只替换测量实现。
