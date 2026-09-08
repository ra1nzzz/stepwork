# STEPWORK W5 执行计划（YTDEV 原子并发）

**版本：v1.0**
**日期：2026-07-22**
**上位文档**：STRATEGY_PLAN.md §3.3（Week 5 原创角度与脚本编辑器）、PRD.md、SYSTEM_SPEC.md、W3_W4_PLAN.md、W6_PLAN.md（W5 先于 W6）
**范式**：YTDEV（7 阶段：调研→PLAN→头脑风暴→终稿→并发实现→三重 REVIEW→原子提交）

---

## 0. 范围现实（Stage 1 调研结论）

| 已具备（W2/W3/W4 产物） | W5 需建 |
|---|---|
| AI Provider（cloud + openai-compatible/ollama），AnalyzeSource 同源范式 | TopicProposal 生成（复用 AI Provider）+ Script 生成 |
| Command Bus（envelope 校验 + 懒加载路由） | 新增 `GenerateTopic` / `GenerateScript` / `SaveScript` 路由 + handler |
| Repos（workspaces/projects/content_versions） | `content_versions` 承载 `topic_proposal` + `script`，**无新表**（版本链用既有 `parent_version_id`） |
| ContentVersion 模型（已有 `parent_version_id` 链） | 自动保存 = 每次保存新建 ContentVersion(parent=prev)；版本比较 = 读链 |
| Rust `dispatch_command` 单一桥接 | **直接复用，W5 不新增任何 Rust 命令** |

**关键路径**：W1→W2→W3-W4→**W5 脚本**→W6 渲染→W9 RC1。用户明确"W5 先做了"（选"完整 W5 功能"），故 W5 在 W6 之前完整交付；W6 的 RenderSource 已 content-agnostic，可直接消费 W5 产出的 `script` ContentVersion。

### 范围决策（必须记录）
- **TopicProposal + Script 复用 ContentVersion**：`content_type` 新增 `"topic_proposal"`（3-5 角度 JSON 列表）与 `"script"`（编辑器正文，TipTap/ProseMirror JSON 或 `{text:...}`）。均落在既有 `content_versions` 表，**无新 migration**。
- **自动保存 = 版本链追加**：`SaveScript` 每次调用新建一条 `script` ContentVersion，`parent_version_id` 指向上一版；刷新/重启后读 `parent` 链即可恢复最新稿（Gate：不丢稿、版本链完整）。
- **版本比较**：读 `content_versions` 的 `parent` 链（或按 project+content_type 倒序），前端 diff 展示；**不新增比较服务**，复用 Repos 查询。
- **脚本编辑器 UI（TipTap）**：W5 完整交付含编辑器 + 自动保存 + 版本历史面板。编辑器选型 **TipTap**（React 生态成熟、JSON 文档模型与 ContentVersion.content 直接对齐）；Lexical 作为备选不实现。
- **FFmpeg/TTS 不属 W5**；W5 产出 `script` 文本，W6 消费之衔接渲染。

---

## 1. 模块编号体系（W5 前缀）

```
L.20 models 扩展        worker/runtime/models.py（TopicProposal / ScriptSpec / ScriptMeta + commandType 枚举扩展）
L.21 AI 生成 handler   worker/runtime/handlers/{generate_topic,generate_script,save_script}.py（复用 AI Provider + Command Bus）
L.22 前端 W5          apps/desktop/src/features/script/（TopicView + ScriptEditor(TipTap) + VersionHistory）+ stores/useScriptStore.ts
```

---

## 2. 接口签名（核心契约）

```python
# L.20 models
class TopicProposalSpec:
    source_version_id: str        # 常来自 transcript / 既有 content_version
    count: int = 5                 # 生成 3-5 个差异化角度
    provider: str | None = None  # 云 AI hint（per-request，零硬编码）

class ScriptSpec:
    proposal_version_id: str | None = None  # 选定 TopicProposal 版
    topic_id: str | None = None     # 选定角度 id
    outline: str | None = None    # 用户增删的角度要点
    style: str = "short_video"      # 模板/语气

# content_versions 内容形态
#  topic_proposal: {"angles":[{"id","title","rationale","hook"}]}
#  script:         TipTap/ProseMirror JSON 或 {"text":"..."}（编辑器原生格式）
# 自动保存链：每版 script 的 parent_version_id 指向上一版
```

---

## 3. Batch 编排（复用基座、无交叉）

```
Batch 0（主代理手写，L.20-L.21 后端）
  → pytest：GenerateTopic 端到端（注入假 AI client → topic_proposal 落库）、
     GenerateScript（proposal → script）、SaveScript（新建版本 + parent 链）、
     版本链读取（最新版一致）
Batch 1（前端，L.22）
  → tsc --noEmit：TopicView（选角度）、ScriptEditor（TipTap + 自动保存）、
     VersionHistory（比较/回滚面板，对齐 PRD）
Batch 2（收口）
  → 写 docs/W5_REVIEW.md（三重 REVIEW ≥8）；原子提交；push -u origin main
```

**共享文件**（主代理统一改）：`worker/runtime/models.py`、`schemas/command-envelope.schema.json`（枚举加 GenerateTopic/GenerateScript/SaveScript）、`worker/runtime/commands/bus.py`、`apps/desktop/src/lib/types.ts` + `tauri.ts`、`docs/W5_REVIEW.md`。

---

## 4. 三角色头脑风暴补丁（P0/P1）

| 角色 | 发现 | 级别 | 合入 |
|---|---|---|---|
| 架构师 | GenerateTopic/Script 复用 W4 AI Provider + Command Bus；**不新增 Rust 命令**，零硬编码密钥（env） | P0 | L.21 |
| 架构师 | 自动保存复用 ContentVersion.parent 链，不新建表/服务；刷新恢复 = 读链最新版 | P0 | L.20/L.21 |
| 安全健壮性 | 云 AI 密钥零硬编码（env/per-request hint）；脚本属用户数据，落库权限同既有 ContentVersion | P0 | L.21 |
| UX | 编辑器自动保存（debounce ~1s）给"已保存"反馈；版本历史可 diff/回滚（回滚 = 将某版内容写回编辑器，不删链） | P0 | L.22 |
| UX | 生成脚本后可一键送入 W6 渲染（source_version_id = script 版 id） | P1 | L.22（衔接 W6） |

---

## 5. 验证矩阵（质量门禁）

| 层 | 命令 | 通过线 |
|---|---|---|
| Python | `pytest worker/tests -q` | 全绿（含 GenerateTopic/Script/SaveScript 端到端、版本链） |
| Python | `mypy worker/`（strict） | 0 issue（用 `--no-incremental` 避缓存钩子） |
| Python | `ruff check worker/` | clean |
| 前端 | `tsc --noEmit` | exit 0 |
| Rust | `cargo check`（仅复用 dispatch_command，W5 无新 Rust 代码） | 同 W6，复用此前结论 |

**STRATEGY Gate 说明**：
- "刷新/重启不丢稿，版本链完整"：单测覆盖 SaveScript 多次 → 链完整、读最新版一致；前端手动/CI-with-secrets 验证。
- 真实 AI 生成需密钥 → 云 AI 走 env 接线 + 单测 mock；本地不实现"确定性占位"因脚本本质需模型（与 W4 analyze 同策略：失败可编辑/切换 Provider）。
- 不伪造生成质量。

---

## 6. 风险与遗留

- **429 限流**：同 W3-W4/W6，子代理可能打断 → Batch 失败主代理手写补齐。
- **本机无 ffmpeg**：不属 W5；W5 只产 script 文本，渲染在 W6。
- **编辑器选型**：定 TipTap（React 成熟、JSON 文档模型对齐 ContentVersion）；Lexical 不实现。
- **Rust link.exe 缺失**：完整 `tauri build` 不行，`cargo check` 仅复用既有 `dispatch_command` 验证。

---

## 7. 下一步（本会话内）

1. 写本 PLAN（已提交为抗中断交付物）。
2. Batch 0：models 扩展 + generate_topic/generate_script/save_script handler + 单测 → ruff/mypy/pytest 全绿 → 原子提交。
3. Batch 1：前端 TopicView + ScriptEditor(TipTap) + VersionHistory → tsc 全绿 → 原子提交。
4. Batch 2：W5_REVIEW（三重 REVIEW）+ 原子提交 + push。
