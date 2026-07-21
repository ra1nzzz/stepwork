# STEPWORK 系统规格说明（SYSTEM SPEC）

**版本：V1.0**  
**状态：架构基线**  
**上位文档：PRODUCT_CHARTER.md、PRD.md**  
**适用范围：桌面宿主、Core、Worker、插件、Agent 互操作、Publisher Engine**

---

## 1. 规格目标

本文件定义 STEPWORK 的系统边界、进程模型、领域模型、接口、任务引擎、插件运行时、Agent 互操作、安全、存储、测试与打包规范。

任何 UI、CLI、MCP、A2A、ACP 或插件实现均不得绕过本规格定义的 Application Services、权限检查和审计机制。

---

## 2. 架构原则

1. Local-first；
2. Agent-Native；
3. Protocol-neutral core；
4. Artifact-first；
5. Human-in-the-loop for irreversible actions；
6. Plugin isolation；
7. Durable local jobs；
8. Explicit provenance；
9. Model/platform/vendor neutrality；
10. Secure by default。

---

## 3. 技术栈基线

### 3.1 桌面宿主

- Tauri 2；
- Rust stable；
- Tauri Commands、Events、Channels；
- Tauri Capabilities；
- 系统密钥链或 Stronghold；
- 签名与自动更新。

Tauri Host 负责高权限桌面能力，前端不得直接获取任意 Shell、文件系统或进程权限。

### 3.2 前端

- React；
- TypeScript；
- Vite；
- Zustand；
- TanStack Query；
- shadcn/ui；
- TipTap 或 Lexical；
- Zod/JSON Schema 验证。

### 3.3 本地服务与 Worker

- Python 3.12；
- Pydantic v2；
- SQLAlchemy 2 或 SQLModel；
- SQLite WAL；
- asyncio；
- FFmpeg；
- Provider SDK；
- 独立 Worker 进程。

### 3.4 浏览器发布执行

- Playwright Python；
- Chrome DevTools Protocol；
- 独立 Browser Profile；
- Publisher Engine Sidecar/Worker。

---

## 4. 总体组件

```text
Desktop UI
    ↓
Tauri Rust Host
    ↓
Application Gateway / Command Bus
    ├── Project Service
    ├── Content Service
    ├── Analysis Service
    ├── Agent Service
    ├── Render Service
    ├── Publish Service
    ├── Plugin Service
    └── Approval Service
          ↓
SQLite + Workspace Files + Secure Credential Store
          ↓
Worker / Provider Plugins / Publisher Engine / Agent Adapters
```

### 4.1 组件职责

#### Desktop UI

- 展示和输入；
- 不持有业务真相；
- 不直接访问数据库；
- 不执行高权限命令。

#### Tauri Rust Host

- 应用生命周期；
- Capabilities；
- Sidecar 启停；
- 安全存储；
- 文件选择；
- 自动更新；
- 本地端点管理。

#### Application Services

- 业务规则唯一实现；
- 事务边界；
- 权限检查；
- 幂等处理；
- 事件发布；
- 审计记录。

#### Worker

- 长任务执行；
- Provider 调用；
- ASR、分析、生成、渲染；
- 任务恢复；
- 临时文件管理。

#### Browser Publisher Engine

- 浏览器 Profile；
- CDP Session；
- 平台填写、预览、确认后发布；
- 结果验证。

---

## 5. Monorepo 结构

```text
stepwork/
├── apps/
│   └── desktop/
│       ├── src/
│       └── src-tauri/
├── core/
│   ├── domain/
│   ├── application/
│   ├── commands/
│   ├── events/
│   ├── policies/
│   └── schemas/
├── worker/
│   ├── runtime/
│   ├── tasks/
│   ├── providers/
│   └── media/
├── agent-interop/
│   ├── cli/
│   ├── mcp/
│   ├── a2a/
│   ├── acp/
│   └── adapters/
├── publisher-engine/
│   ├── browser/
│   ├── dom/
│   ├── uploader/
│   ├── runtime/
│   └── rpc/
├── sdk/
│   ├── python/
│   ├── typescript/
│   ├── plugin/
│   └── agent-adapter/
├── plugins/
│   ├── official/
│   ├── examples/
│   └── registry/
├── schemas/
├── migrations/
├── tests/
├── docs/
└── scripts/
```

---

## 6. 进程模型

### 6.1 必需进程

1. `stepwork-desktop`：Tauri 主进程；
2. `stepwork-worker`：内容处理长任务；
3. `stepwork-publisher`：按需启动的浏览器执行进程；
4. `stepwork-agent-host`：可与 Worker 合并，后续用于 ACP/A2A 长会话。

### 6.2 通信

首版采用本地 JSON-RPC 或长度前缀消息：

- Tauri ↔ Worker：stdio 或本地受限 Socket；
- Tauri ↔ Publisher：stdio 或本地受限 Socket；
- CLI/MCP：进入同一 Gateway；
- A2A：仅在用户开启远程 Agent 服务后监听 HTTP(S)；
- ACP：本地 Agent 默认 JSON-RPC over stdio。

### 6.3 心跳与恢复

每个 Sidecar 必须：

- 启动后发送 `runtime.ready`；
- 每 5 秒心跳；
- 失联后 Host 标记为 DEGRADED；
- 运行中任务保存在数据库；
- 重启后领取可恢复任务；
- 不依赖内存保存关键状态。

---

## 7. Application Services 与 Command Bus

### 7.1 Command Envelope

```json
{
  "commandId": "uuid",
  "commandType": "AnalyzeSource",
  "schemaVersion": "1",
  "actor": {
    "type": "user|agent|plugin|system",
    "id": "actor-id"
  },
  "source": "ui|cli|mcp|a2a|acp|plugin|scheduled",
  "workspaceId": "uuid",
  "projectId": "uuid",
  "idempotencyKey": "optional-string",
  "payload": {},
  "requestedAt": "ISO-8601"
}
```

### 7.2 核心 Commands

- CreateWorkspace；
- CreateProject；
- ImportSource；
- TranscribeSource；
- AnalyzeSource；
- ProposeTopics；
- GenerateScript；
- SaveContentVersion；
- CreatePlatformVariant；
- CreateRenderJob；
- ConnectAgent；
- InvokeAgent；
- AcceptAgentArtifact；
- InstallPlugin；
- PreparePublish；
- ApprovePublish；
- ExecutePublish；
- ExportArtifact；
- DeleteAsset。

### 7.3 规则

- 每个 Command 必须验证 Schema；
- 每个 Command 必须解析 Actor 和 Scope；
- 有副作用的 Command 支持幂等键；
- 高风险 Command 可以返回 ApprovalRequired；
- 协议 Adapter 不得直接调用数据库。

---

## 8. 领域数据模型

### 8.1 内容领域

#### Workspace

- id；
- name；
- root_path；
- settings；
- created_at；
- archived_at。

#### ContentProject

- id；
- workspace_id；
- title；
- status；
- brand_profile_id；
- current_content_version_id；
- created_at；
- updated_at。

#### SourceAsset

- id；
- project_id；
- kind；
- local_uri；
- original_uri；
- content_hash；
- rights_declaration；
- metadata；
- created_at。

#### Transcript

- id；
- source_asset_id；
- provider_id；
- text；
- segments；
- language；
- version；
- created_at。

#### AnalysisReport

- id；
- project_id；
- source_asset_ids；
- mode；
- schema_version；
- structured_content；
- citations；
- producer；
- status；
- created_at。

#### TopicProposal

- id；
- project_id；
- title；
- audience；
- thesis；
- differentiation；
- risks；
- source_artifact_ids。

#### ContentVersion

- id；
- project_id；
- parent_version_id；
- content_type；
- content；
- content_hash；
- producer；
- created_at。

#### PlatformVariant

- id；
- content_version_id；
- platform；
- title；
- body；
- tags；
- cover_text；
- validation_status。

#### ProvenanceRecord

- id；
- subject_type；
- subject_id；
- source_ids；
- model_calls；
- agent_tasks；
- plugin_executions；
- user_edits；
- ai_label_state。

### 8.2 Agent 领域

#### AgentConnection

- id；
- protocol：cli/mcp/a2a/acp/custom；
- endpoint_or_command；
- local_or_remote；
- trust_level；
- auth_ref；
- status；
- capabilities。

#### AgentTask

- id；
- initiator；
- target_agent_id；
- project_id；
- task_type；
- input_artifact_ids；
- state；
- progress；
- cost；
- timeout_at；
- correlation_id。

#### AgentSession

- id；
- agent_connection_id；
- project_id；
- external_session_id；
- status；
- started_at；
- ended_at。

#### AgentArtifact

- id；
- project_id；
- artifact_type；
- schema_version；
- producer_agent_id；
- content_uri_or_json；
- source_refs；
- trust_level；
- content_hash；
- review_state。

#### ApprovalRequest

- id；
- actor；
- action_type；
- target；
- requested_scope；
- risk_summary；
- expires_at；
- status；
- decision_actor。

### 8.3 执行领域

#### Job

- id；
- job_type；
- state；
- payload；
- progress；
- attempt_count；
- max_attempts；
- lease_owner；
- lease_expires_at；
- error_code；
- result_artifact_ids。

#### PublishJob

- id；
- platform_variant_id；
- social_account_id；
- plugin_id；
- plugin_version；
- state；
- approval_id；
- evidence_artifact_ids；
- remote_content_id。

---

## 9. 本地存储

### 9.1 数据库

- SQLite WAL；
- 外键开启；
- 所有迁移使用版本号；
- 写入事务由 Application Service 管理；
- 数据库不存放大体积视频二进制。

### 9.2 文件目录

```text
STEPWORK_HOME/
├── stepwork.db
├── workspaces/<workspace-id>/projects/<project-id>/
├── browser-profiles/
├── plugins/
├── plugin-data/
├── agents/
├── cache/
├── temp/
├── logs/
└── backups/
```

### 9.3 文件规则

- 资产使用 UUID 路径，显示名保存在数据库；
- 所有导入文件计算 SHA-256；
- 项目导出包含 manifest 和 Schema Version；
- 临时目录可自动清理；
- 删除项目先进入可恢复区，再按策略彻底删除。

---

## 10. 任务引擎

### 10.1 状态

```text
PENDING
READY
RUNNING
WAITING_EXTERNAL
WAITING_USER
RETRY_SCHEDULED
COMPLETED
FAILED_FINAL
CANCELLED
```

业务阶段通过 `stage` 字段记录：

- DOWNLOADING；
- TRANSCRIBING；
- ANALYZING；
- DELEGATING；
- GENERATING；
- SYNTHESIZING；
- RENDERING；
- PUBLISHING；
- VERIFYING。

### 10.2 Lease

Worker 领取任务时写入：

- lease_owner；
- lease_expires_at；
- heartbeat_at。

Worker 崩溃后，Lease 到期任务可重新领取。

### 10.3 幂等

- 输入文件通过 Hash 去重；
- Command 可带 idempotency_key；
- PublishApproval 绑定 content_hash；
- 已完成发布任务不得因重试再次点击发布；
- 每个 Provider 调用记录 request fingerprint。

### 10.4 取消

- Job 必须定期检查 cancellation token；
- FFmpeg 子进程必须可终止；
- 外部 Agent 任务发送取消请求后进入 CANCEL_REQUESTED；
- 无法取消的外部调用需在 UI 明示。

---

## 11. Artifact 与 Provenance 规格

### 11.1 Artifact Envelope

```json
{
  "artifactId": "uuid",
  "artifactType": "content-analysis",
  "schemaVersion": "1",
  "projectId": "uuid",
  "producer": {
    "type": "user|model|agent|plugin|system",
    "id": "producer-id",
    "protocol": "optional"
  },
  "content": {},
  "contentUri": null,
  "sourceRefs": [],
  "trustLevel": "external-unverified",
  "contentHash": "sha256",
  "createdAt": "ISO-8601"
}
```

### 11.2 Trust Level

- trusted-local；
- trusted-remote；
- verified-external；
- external-unverified；
- generated-unverified；
- human-reviewed。

### 11.3 Artifact 接受流程

外部 Agent Artifact 默认进入 `PENDING_REVIEW`。用户可：

- 接受为正式 Artifact；
- 合并到 ContentVersion；
- 标记仅作参考；
- 拒绝；
- 请求 Agent 修订。

---

## 12. 插件规格

### 12.1 插件类型

- source-provider；
- ai-provider；
- speech-provider；
- analyzer；
- renderer；
- publisher；
- exporter；
- agent-adapter。

### 12.2 Manifest

```json
{
  "id": "org.stepwork.renderer.example",
  "name": "Example Renderer",
  "version": "0.1.0",
  "apiVersion": "1",
  "type": "renderer",
  "license": "Apache-2.0",
  "entry": {
    "runtime": "python",
    "module": "plugin.main"
  },
  "permissions": [
    "files:read-project",
    "files:write-artifacts",
    "process:ffmpeg"
  ],
  "capabilities": ["render:vertical-caption-v1"],
  "platforms": ["windows", "macos"]
}
```

### 12.3 权限

权限命名：`resource:action[:scope]`

示例：

- files:read-selected；
- files:read-project；
- files:write-artifacts；
- network:domain:api.example.com；
- secrets:read-own；
- browser:control；
- publisher:prepare；
- publisher:execute；
- agent:invoke；
- process:ffmpeg。

### 12.4 加载

1. 读取 Manifest；
2. 校验签名和哈希；
3. 校验 API Version；
4. 展示权限；
5. 用户授权；
6. 在独立运行时启动；
7. 注册 Capability；
8. 进行 Health Check。

### 12.5 隔离

- 第三方 Python/Node 插件独立进程；
- 不允许任意继承父进程环境变量；
- 只通过受控 RPC 获取凭据；
- 高权限插件不可加载远程代码；
- 插件输出必须验证 Schema。

---

## 13. Agent 互操作规格

### 13.1 内部优先

CLI、MCP、A2A 和 ACP 只是 Adapter。核心对象是：

- Command；
- AgentTask；
- AgentArtifact；
- InvocationContext；
- ApprovalRequest。

### 13.2 CLI

命令格式：

```text
stepwork <resource> <action> [arguments]
```

必须支持：

- `--json`；
- `--idempotency-key`；
- `--workspace`；
- `--project`；
- `--wait`；
- `--timeout`；
- `--non-interactive`。

退出码：

- 0 成功；
- 2 参数错误；
- 3 未认证；
- 4 无权限；
- 5 资源不存在；
- 6 需要审批；
- 7 业务校验失败；
- 8 外部 Provider 失败；
- 9 任务超时；
- 10 内部错误。

### 13.3 MCP Server

首版暴露：

**Tools**

- list_projects；
- get_project；
- import_source；
- analyze_source；
- propose_topics；
- generate_script；
- create_render_job；
- get_job_status；
- list_artifacts；
- export_artifact；
- prepare_publish。

**Resources**

- `stepwork://workspace/{id}`；
- `stepwork://project/{id}`；
- `stepwork://source/{id}`；
- `stepwork://analysis/{id}`；
- `stepwork://content-version/{id}`；
- `stepwork://artifact/{id}`。

**Prompts**

- analyze-reference-content；
- generate-original-angles；
- rewrite-in-brand-voice；
- review-content-risk。

规则：

- Tool input/output 使用 JSON Schema；
- Tool 不直接暴露凭据；
- 高风险工具返回 ApprovalRequest；
- 本地默认 stdio；
- 可选 HTTP 只在用户开启后使用认证。

### 13.4 MCP Client

- Server 配置保存在 AgentConnection；
- 每个 Server 独立 Scope；
- 结果进入 ExternalArtifact；
- Tool 注解不作为可信安全声明；
- 调用前执行输出域和数据外传检查。

### 13.5 A2A

STEPWORK 映射：

- Agent Card → AgentConnection + AgentCapability；
- A2A Task → AgentTask；
- Message/Part → AgentTask Message；
- Artifact → AgentArtifact；
- Context → AgentSession/Correlation ID。

首个 Agent Skills：

- content-reference-analysis；
- original-topic-proposal；
- script-drafting；
- brand-voice-rewriting；
- media-draft-rendering；
- publish-preparation。

A2A Server 默认不暴露 Publisher Execute。

### 13.6 ACP Client

- 本地 Agent 作为子进程；
- JSON-RPC over stdio；
- Session 绑定 Project；
- 支持流式进度；
- 支持权限请求；
- 支持文件差异和 Artifact；
- 可向 Agent 提供本地 STEPWORK MCP Server；
- Agent 不直接读取整个 Workspace，必须通过 Root/Scope。

首版 ACP 不承担通用聊天入口，优先用于：

- 插件开发；
- Publisher 诊断；
- 模板修改；
- 项目文件受控编辑。

---

## 14. Browser Publisher Engine 规格

### 14.1 来源

现有 `media-auto-pilot` 作为迁移基础，其 CDP Session、DOM 定位、多策略上传和平台适配器思想可复用；CLI 和 Linux 云环境专用实现需重构。

### 14.2 新模块

```text
publisher-engine/
├── profile_manager.py
├── browser_runtime.py
├── session_manager.py
├── dom_snapshot.py
├── locator.py
├── uploader.py
├── evidence.py
├── approval.py
├── platform_runtime.py
└── rpc_server.py
```

### 14.3 PublisherAdapter

```python
class PublisherAdapter:
    async def health_check(self): ...
    async def login(self, account_context): ...
    async def validate_content(self, content): ...
    async def prepare(self, content): ...
    async def fill(self, content): ...
    async def preview(self): ...
    async def publish(self, approval): ...
    async def verify(self, publish_result): ...
```

### 14.4 Browser Profile

每个 Profile 绑定：

- Workspace；
- Platform；
- SocialAccount；
- Browser Type；
- User Data Directory；
- 动态 CDP 端口；
- 最近健康状态。

禁止多个账号共享默认 Profile。

### 14.5 执行等级

- OPEN_ONLY；
- FILL_AND_PREVIEW；
- CONFIRMED_PUBLISH；
- POLICY_CONTROLLED_AUTO（实验）。

### 14.6 删除项

不得包含：

- Canvas/WebGL 指纹伪装；
- 隐藏 WebDriver 的反检测脚本；
- 多账号轮换规避风控；
- 无确认最终发布；
- 默认 `--no-sandbox`。

---

## 15. 安全规格

### 15.1 凭据

- API Key、Token 和账号凭据存入系统密钥链；
- 数据库只保存 credential_ref；
- 插件通过短期凭据句柄调用；
- 日志脱敏。

### 15.2 Scope

```text
workspace:read
workspace:write
project:read
project:write
source:read
source:export
analysis:create
script:write
render:create
agent:invoke
publisher:prepare
publisher:execute
plugin:install
```

### 15.3 本地端点

- 默认 Loopback；
- 使用随机短期 Token；
- 端口写入权限受限文件；
- 远程访问默认关闭；
- HTTP 远程模式必须 TLS；
- 不在 Agent Card 或配置中嵌入静态敏感凭据。

### 15.4 Prompt Injection

- 外部文本标记为 untrusted_content；
- 系统指令与项目内容分层；
- 外部内容不能调用高风险 Command；
- Tool/Agent 结果在进入下游模型前经过内容和权限边界；
- 发布不接受外部内容中的隐藏指令。

### 15.5 审计

AuditEvent 至少记录：

- actor；
- source protocol；
- command；
- target；
- requested scope；
- approval；
- result；
- timestamp；
- correlation_id。

---

## 16. 错误模型

统一 Error Envelope：

```json
{
  "code": "AGENT_PERMISSION_DENIED",
  "message": "该 Agent 无权读取此项目",
  "retryable": false,
  "details": {},
  "correlationId": "uuid",
  "userAction": "在 Agent Connections 中授予 project:read 权限"
}
```

错误类别：

- VALIDATION_*；
- AUTH_*；
- PERMISSION_*；
- STORAGE_*；
- PROVIDER_*；
- AGENT_*；
- PLUGIN_*；
- RENDER_*；
- PUBLISH_*；
- INTERNAL_*。

所有错误必须区分：

- 用户可修复；
- 可自动重试；
- 需要重新登录；
- 需要插件升级；
- 最终失败。

---

## 17. 可观测性

### 17.1 日志

- JSON Lines；
- correlation_id；
- job_id；
- actor；
- component；
- level；
- sanitized fields。

### 17.2 指标

本地指标：

- Job 成功率和耗时；
- Provider 错误率；
- AgentTask 完成率；
- 插件健康；
- Browser Session 成功率；
- 数据迁移结果。

### 17.3 Trace

Agent 与工作流 Trace：

```text
User → Command → AgentTask → External Agent → Artifact → ContentVersion → RenderJob
```

---

## 18. 测试规格

### 18.1 单元测试

- Domain rules；
- Command handlers；
- Scope policies；
- Schema validation；
- Job state transitions；
- Artifact trust transitions。

### 18.2 集成测试

- Tauri ↔ Worker；
- CLI ↔ Application Services；
- MCP Tools；
- SQLite migration；
- Provider mock；
- Plugin load/unload；
- Publisher RPC。

### 18.3 契约测试

- Plugin Manifest；
- Provider SDK；
- MCP input/output Schema；
- A2A mapping；
- ACP session adapter；
- Artifact Schema 向后兼容。

### 18.4 E2E

- 导入→分析→脚本→渲染→导出；
- Agent 调用→Artifact→接受；
- 应用重启恢复任务；
- 凭据授权；
- Publisher 填写与预览；
- 升级与数据迁移。

### 18.5 安全测试

- 插件越权；
- Agent 越权；
- Prompt Injection；
- 路径穿越；
- 恶意项目包；
- 日志敏感数据；
- 未授权本地端口访问。

---

## 19. 打包与更新

### 19.1 Windows MVP

- x64；
- Tauri 安装包；
- Python Worker 打包为 Sidecar；
- FFmpeg 作为受控外部二进制；
- 首次启动自检；
- 可选下载模型，不内置超大模型。

### 19.2 更新

- Core、Sidecar、官方插件独立版本；
- 数据库迁移前自动备份；
- 插件 API Version 检查；
- 更新签名验证；
- 支持回滚到上一应用版本，但不逆向回滚数据库，需恢复备份。

---

## 20. Media Auto Pilot 迁移矩阵

| 模块 | 决策 | 说明 |
|---|---|---|
| ChromeProcess | 重写后保留 | 跨平台、动态端口、Profile 隔离 |
| BrowserSession | 重构复用 | 保留 CDP 持久连接思想 |
| DOMSnapshot | 重构复用 | 增加 iframe、Shadow DOM、快照版本 |
| SmartLocator | 重构复用 | 不用于无确认不可逆操作 |
| FileUploader | 重构复用 | 修复上传验证和进度 |
| BasePlatform | 替换为 PublisherAdapter | 强类型和结构化状态 |
| DouyinPlatform | 迁移为官方插件 | 默认 Fill and Preview |
| FrequencyController | 删除规避导向逻辑 | 仅保留正常速率限制可重新设计 |
| Stealth scripts | 删除 | 不进入正式产品 |
| CLI | 保留诊断用途 | 正式调用走统一 Command Bus |

---

## 21. 架构决策记录（ADR）清单

- ADR-001：Tauri 2 + React；
- ADR-002：Python Sidecar；
- ADR-003：SQLite WAL；
- ADR-004：Command Bus 与 Application Services；
- ADR-005：Artifact-first；
- ADR-006：AGPL Core + Apache SDK；
- ADR-007：CLI/MCP MVP，A2A/ACP 后置实现但数据模型前置；
- ADR-008：Publisher 默认 Fill and Preview；
- ADR-009：插件独立进程；
- ADR-010：Media Auto Pilot 作为迁移基础而非 Core。

