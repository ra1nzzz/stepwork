/**
 * STEPWORK W1.5 类型定义
 * 与 Python worker/runtime/handlers/health.py 及 Rust sidecar 错误枚举对称
 */

export type HealthState = "ok" | "degraded" | "down";

/**
 * Worker 健康状态（v1.1 Patch-U3 + P1-架构-5）
 */
export interface HealthStatus {
  status: HealthState;
  version: string;
  protocol_version: string;
  uptime_seconds: number;
  pid: number;
  last_heartbeat_at: string | null;
  startup_duration_ms: number;
  active_jobs: number;
  degraded_reasons: string[];
  runtime_info: Record<string, unknown>;
}

/**
 * Sidecar 错误分类（v1.1 Patch-U2 / Patch-S2）
 */
export type SidecarErrorKind =
  | "PythonMissing"
  | "SpawnFailed"
  | "HandshakeTimeout"
  | "RpcProtocolError"
  | "WorkerCrashed"
  | "FrameTooLarge"
  | "ParseError"
  | "Shutdown"
  | "Unknown";

/**
 * 结构化 Sidecar 错误（P1-架构-6，对齐 SYSTEM_SPEC §16 Error Envelope）
 */
export interface SidecarError {
  kind: SidecarErrorKind;
  code: string;
  message: string;
  retryable: boolean;
  details: Record<string, unknown> | null;
  correlation_id: string | null;
}

/**
 * 应用信息（Tauri get_app_info 返回）
 *
 * v1.1 / W2: 新增 restart_count 与 last_crash_at，供运维 footer 展示
 * sidecar 自愈重启次数与最近一次崩溃时间（对齐 Rust AppState 字段）。
 */
export interface AppInfo {
  version: string;
  platform: string;
  stepwork_home: string;
  /** sidecar 自愈重启累计次数（来自 Rust AppState.restart_count） */
  restart_count: number;
  /** 最近一次崩溃 UTC 时间 ISO 字符串；从未崩溃时为 null */
  last_crash_at: string | null;
}

/**
 * ===== W3 / W4 领域类型（对齐 worker/runtime/models.py + schemas） =====
 */

export type JobState =
  | "pending"
  | "leased"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "cancelled-requested"
  | "expired";

export type JobStage =
  | "downloading"
  | "transcribing"
  | "analyzing"
  | "delegating"
  | "generating"
  | "proposing"
  | "scripting"
  | "synthesizing"
  | "rendering"
  | "publishing"
  | "verifying";

/** Command Bus 信封（对齐 schemas/command-envelope.schema.json） */
export interface CommandEnvelope {
  commandId: string;
  commandType:
    | "ImportSource"
    | "TranscribeSource"
    | "AnalyzeSource"
    | "GenerateTopic"
    | "GenerateScript"
    | "SaveScript"
    | "CreateRenderJob"
    | "CancelJob"
    | "GetConfig"
    | "UpdateConfig"
    | "GetProvenance"
    | "ListAgentTasks"
    | "ListAgentArtifacts"
    | "GetAgentTask"
    | "ExportDiagnosticsBundle"
    | "ListPlugins"
    | "GetPluginManifest"
    | "EnablePlugin"
    | "DisablePlugin"
    | "ExportProject"
    | "ImportProject"
    | "ListProjects"
    | "GetProject"
    | "GetJobStatus"
    | "ListJobs"
    | "InstallPlugin"
    | "CreateProject"
    | "DeleteAsset"
    | "BackupWorkspace"
    | "RestoreWorkspace"
    | "CreateBrandProfile"
    | "UpdateBrandProfile"
    | "ListBrandProfiles"
    | "SetProjectBrandProfile"
    | "SaveAnalysis"
    | "ListContentVersions"
    | "GetContentVersion"
    | "CreateWorkspace"
    | "RenameWorkspace"
    | "ArchiveWorkspace"
    | "ListWorkspaces"
    | "CreatePlatformVariant"
    | "ListPlatformVariants"
    | "ExportBundle"
    | "ListRenderTemplates"
    | "ListSourceAssets"
    | "GetSourceAsset"
    | "RunCleanup"
    | "ListAuditEvents"
    | "EditParagraph"
    | "PreviewPluginManifest"
    | "UninstallPlugin"
    | "CheckPluginHealth"
    | "CreateApprovalRequest"
    | "ListApprovalRequests"
    | "DecideApprovalRequest"
    | "SetProjectTags"
    | "DiffContentVersions"
    | "ImportBrandScript"
    | "ListBrandScripts"
    | "DeleteBrandScript"
    | "RecordPreference"
    | "ListAgentConnections"
    | "SetAgentConnectionStatus"
    | "DeleteAgentConnection"
    | "AddMcpServer"
    | "ListMcpTools"
    | "CallMcpTool"
    | "RequestPublishAuthorization"
    | "RecordPublishResult"
    | "ListPublishJobs"
    | "BuildPlatformFillPackage";
  schemaVersion: string;
  actor: { type: "user" | "agent" | "plugin" | "system" | "desktop"; id: string };
  source: string;
  workspaceId: string;
  projectId: string | null;
  idempotencyKey: string | null;
  payload: Record<string, unknown>;
  requestedAt: string;
}

/** Command Bus 统一返回（对齐 worker/runtime/models.py CommandResult） */
export interface CommandResult {
  ok: boolean;
  commandId: string;
  job_id: string | null;
  artifact_ids: string[];
  error: string | null;
  detail: Record<string, unknown> | null;
}

export interface MediaMeta {
  duration_seconds?: number;
  width?: number;
  height?: number;
  codec?: string;
  sample_rate?: number;
  channels?: number;
}

export type ImportStatus = "pending" | "importing" | "done" | "error";

export interface SourceAsset {
  id: string;
  project_id: string;
  kind: string;
  local_uri: string;
  original_uri: string | null;
  content_hash: string;
  import_status: ImportStatus;
  created_at: string;
  media_meta: MediaMeta | null;
}

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
}

export interface Transcript {
  version_id: string;
  source_asset_id: string | null;
  language: string | null;
  segments: TranscriptSegment[];
  text: string;
  provider: string;
  created_at: string;
}

export type AnalysisStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed";

/**
 * 完整分析报告数据（对齐 worker/runtime/analysis/schema.py ANALYSIS_SCHEMA，
 * Tranche 2 新增 hook / structure / risks 三个 required 字段）。
 * 报告以 JSON string 落库为 content_versions(analysis)，前端经
 * GetContentVersion 拉取全文后解析为本类型。
 */
/** PRD-ANA-005：关键结论的来源锚点（可跳转时间戳或逐字稿片段） */
export interface Citation {
  claim: string;
  start_sec: number | null;
  scene_index: number | null;
  quote: string | null;
}

export interface AnalysisReportData {
  summary: string;
  /** 开头钩子（Tranche 2 新增；旧版本报告可能缺失 → null） */
  hook: string | null;
  /** 内容结构骨架（Tranche 2 新增） */
  structure: string[];
  topics: string[];
  key_points: string[];
  /** 风险点（Tranche 2 新增） */
  risks: string[];
  sentiment: string | null;
  suggested_title: string | null;
  suggested_tags: string[];
  target_audience: string | null;
  provider: string;
  model: string;
  confidence: number | null;
  /** PRD-ANA-005：来源引用（旧版本报告可能缺失） */
  citations?: Citation[];
}

/** 一次分析任务的 UI 侧包装（store 内条目） */
/** 分析模式（PRD-ANA-002 快速 / PRD-ANA-003 精确） */
export type AnalysisMode = "quick" | "precise";

export interface AnalysisReport {
  status: AnalysisStatus;
  /** 本次分析所用模式（精确模式结合场景切分/关键帧） */
  mode: AnalysisMode;
  /** 精确模式检出的场景数（quick 恒为 0） */
  sceneCount: number;
  /** 分析产物 content_versions(analysis) 的版本 id */
  versionId: string | null;
  /** 完整报告数据（GetContentVersion 拉取解析；失败时为 null） */
  data: AnalysisReportData | null;
  /** 费用透明：执行后由 detail.invocation 回填 */
  invocation: ProviderInvocation | null;
  provider: string | null;
  model: string | null;
  confidence: number | null;
  created_at: string | null;
  error: string | null;
}

/**
 * ===== W5 领域类型（选题角度 + 脚本编辑器，对齐 worker/runtime/models.py） =====
 */

export interface TopicAngle {
  id: string;
  title: string;
  rationale: string;
  hook: string;
  /** PRD-SCR-001：目标受众（旧版本提案可能缺失） */
  audience?: string | null;
  /** PRD-SCR-001：核心观点/立场 */
  stance?: string | null;
  /** PRD-SCR-001：风险点 */
  risks?: string[];
}

/**
 * 相似度提醒（PRD-SCR-004 重复选题 / PRD-SCR-005 原创性）。
 * 语义是「提醒用户确认」，不是判定——PRD 明确要求不做法律结论。
 */
export interface SimilarityWarning {
  kind: "duplicate_topic" | "similar_script";
  ref_id: string;
  score: number;
  label: string;
  message: string;
  angle_id?: string;
}

export interface TopicProposal {
  angles: TopicAngle[];
}

export interface ScriptContent {
  title: string;
  body: string;
}

/** PRD-SCR-006：版本差异（AI 初稿 vs 最终稿） */
export interface DiffLine {
  op: "equal" | "insert" | "delete";
  text: string;
  before_line: number | null;
  after_line: number | null;
}

export interface VersionDiff {
  base_version_id: string;
  target_version_id: string;
  base_is_ai_draft: boolean;
  base_title: string;
  target_title: string;
  lines: DiffLine[];
  summary: { added: number; removed: number; unchanged: number };
}

/** 版本链节点（VersionHistory 用；对齐 content_versions 的 parent 链） */
export interface ScriptVersionRef {
  id: string;
  parent_version_id: string | null;
  created_at: string;
  producer_kind: string | null;
}

/** GenerateTopic payload（对齐 TopicProposalSpec） */
export interface GenerateTopicPayload {
  source_version_id: string;
  /** PRD-SCR-001：3—5 个角度（后端强校验，越界即 INVALID_ARGUMENT） */
  count?: number;
  provider?: Record<string, unknown> | null;
  /** PRD-BRD-002：生成时是否启用项目绑定的品牌档 */
  use_brand_profile?: boolean;
}

/** GenerateScript payload（对齐 ScriptSpec） */
export interface GenerateScriptPayload {
  proposal_version_id?: string | null;
  topic_id?: string | null;
  outline?: string | null;
  style?: string;
  provider?: Record<string, unknown> | null;
  /** PRD-BRD-002：生成时是否启用项目绑定的品牌档 */
  use_brand_profile?: boolean;
}

/** SaveScript payload（自动保存 = 版本链追加） */
export interface SaveScriptPayload {
  content: string | Record<string, unknown>;
  parent_version_id?: string | null;
}

/** CreateRenderJob payload（对齐 RenderSpec / render_source.py） */
export interface CreateRenderJobPayload {
  source_version_id: string;
  template?: string;
  /** PRD-REN-005：画幅比例（9:16 / 16:9 / 1:1），后端据此解析 resolution */
  aspect?: string;
  tts_engine?: "synthesize" | "user_audio";
  tts_provider?: string | null;
  user_audio_uri?: string | null;
  background_uri?: string | null;
}

/** CancelJob payload（对齐 cancel_job.py） */
export interface CancelJobPayload {
  job_id: string;
}

/** 渲染产物元数据（对齐 VideoDraftMeta / video_draft 内容） */
export interface VideoDraftMeta {
  video_uri: string;
  duration_seconds: number;
  template: string;
  tts_engine: string;
  resolution: [number, number];
  fps: number;
  source_version_id: string;
}

/** 前端 provider 选择（provider-switch UI） */
export type ProviderKind = "cloud" | "openai-compatible" | "ollama";

export interface ProviderConfig {
  kind: ProviderKind;
  base_url: string;
  /** 来自运行时输入，绝不写死 */
  api_key: string;
  model: string;
}

/** 后端 GetConfig 返回的「已合并 + 掩码」配置视图。
 *  密钥一律掩码（"••••"），永不回显明文。 */
export interface ConfigView {
  config: Record<string, unknown>;
  resolved: {
    ai: { provider: string | null; model: string | null; hasKey: boolean };
    asr: { provider: string | null; hasKey: boolean };
    tts: { provider: string | null; model: string | null; hasKey: boolean };
  };
}

/** getConfig / updateConfig 的统一返回形状（与 SettingsView 中本地别名一致）。 */
export interface ConfigResult {
  ok: boolean;
  config?: Record<string, unknown>;
  resolved?: ConfigView["resolved"];
  error?: string;
}

/**
 * ===== Tranche 1 类型（ListJobs / InstallPlugin / job.progress 通知） =====
 */

/** ListJobs payload（对齐 worker/runtime/handlers/queries.py，state 为小写 JobState） */
export interface ListJobsPayload {
  states?: JobState[];
  limit?: number;
}

/** 持久化任务行（与 GetJobStatus / ListJobs 的 job dict 同构） */
export interface PersistedJob {
  id: string;
  job_type: string;
  state: JobState;
  stage: JobStage | null;
  progress: number;
  attempt_count: number;
  error_code: string | null;
  created_at: string;
  updated_at: string;
}

/** InstallPlugin payload（path 为含 manifest.json 的目录绝对路径） */
export interface InstallPluginPayload {
  path: string;
}

/** 插件 manifest（对齐 worker installed_plugins.manifest_json 解析结果） */
export interface PluginManifest {
  id?: string;
  name?: string;
  version?: string;
  apiVersion?: number | string;
  permissions?: string[];
  [key: string]: unknown;
}

/** ListPlugins / InstallPlugin 返回的单条插件行
 *  （manifest 解析失败时为 null，status 覆盖为 'error'） */
/**
 * PRD-PUB-003 填充包（ADR-008 FILL_AND_PREVIEW）。
 * auto_publish 恒为 false —— 消费方据此知道不得点击最终发布。
 */
export interface FillPackageIssue {
  field: string;
  level: "error" | "warning";
  message: string;
}

export interface FillPackage {
  platform: string;
  platform_label: string;
  mode: string;
  auto_publish: false;
  requires_manual_publish: true;
  fields: { title: string; body: string; tags: string[] };
  assets: { video: string | null; cover: string | null };
  constraints: Record<string, number>;
  issues: FillPackageIssue[];
  ready: boolean;
}

/** PRD-PLG-004 插件信任分级 */
export type PluginTrustTier =
  | "official"
  | "verified"
  | "community"
  | "experimental";

export interface InstalledPlugin {
  id: string;
  enabled: boolean;
  status: string;
  manifest: PluginManifest | null;
  installed_at: string;
  error_message: string | null;
  /** PRD-PLG-004：信任等级（UI 必须明确显示） */
  trust_tier: PluginTrustTier;
  /** PRD-PLG-005：最近加载 / 最近测试时间与结果 */
  last_loaded_at: string | null;
  last_checked_at: string | null;
  last_check_result: string | null;
}

/**
 * 审批请求（PRD-AGT-008 / §9.2）。字段与 approval_requests 表对齐，
 * 覆盖 §9.2 要求展示的七要素。
 */
export interface ApprovalRequest {
  id: string;
  /** §9.2 请求者（协议或插件） */
  actor: string;
  /** §9.2 将执行的动作 */
  action_type: string;
  /** §9.2 目标 Workspace/Project */
  target: string;
  /** §9.2 一次性或持久授权范围 */
  requested_scope: string | null;
  /** §9.2 费用或风险 */
  risk_summary: string | null;
  /** §9.2 将读取、修改或上传的数据 */
  payload: Record<string, unknown>;
  /** §9.2 有效期 */
  expires_at: string | null;
  status: "pending" | "approved" | "rejected" | "expired" | "consumed";
  decision_actor: string | null;
  decision_at: string | null;
  created_at: string;
}

/** worker → Rust → 前端的 job.progress 通知 params */
export interface JobProgressParams {
  job_id: string;
  job_type: string;
  state: JobState;
  stage: string | null;
  progress: number;
  error_code: string | null;
}

/** Tauri 事件 'worker-notification' 的 payload 形状 */
export interface WorkerNotification {
  method: string;
  params: Record<string, unknown>;
}

/**
 * ===== Tranche 2 类型（BrandProfile / 版本查询 / Workspace / 发布 / 费用透明） =====
 */

/** 费用透明：命令成功后 detail.invocation（provider/model/粗估费用） */
export interface ProviderInvocation {
  provider: string;
  model: string;
  estimated_cost: number | null;
}

/** 渲染成功 detail.artifacts（绝对路径；字幕/音频可能缺失） */
export interface RenderArtifacts {
  video: string;
  subtitles: string | null;
  audio: string | null;
}

/** 品牌档案（对齐 migrations/0005 brand_profiles；出参全字段 camelCase） */
export interface BrandProfile {
  id: string;
  workspaceId: string;
  name: string;
  positioning: string;
  audience: string;
  tone: string;
  contentPillars: string[];
  bannedExpressions: string[];
  createdAt: string;
  updatedAt: string;
}

/** CreateBrandProfile payload */
export interface CreateBrandProfilePayload {
  name: string;
  positioning?: string;
  audience?: string;
  tone?: string;
  contentPillars?: string[];
  bannedExpressions?: string[];
}

/** UpdateBrandProfile payload（除 profileId 外均可选） */
export interface UpdateBrandProfilePayload extends Partial<CreateBrandProfilePayload> {
  profileId: string;
}

/** SetProjectBrandProfile payload（profileId 为 null 表示解除关联） */
export interface SetProjectBrandProfilePayload {
  projectId: string;
  profileId: string | null;
}

/** SaveAnalysis payload（content 为报告 JSON string；版本链仿 SaveScript） */
export interface SaveAnalysisPayload {
  projectId?: string;
  content: string;
  parentVersionId?: string | null;
}

/** ListContentVersions payload */
export interface ListContentVersionsPayload {
  projectId: string;
  contentType?: string;
  limit?: number;
}

/** ListContentVersions 返回的版本摘要（preview 为内容前 200 字符） */
export interface ContentVersionSummary {
  id: string;
  content_type: string;
  parent_version_id: string | null;
  created_at: string;
  producer: Record<string, unknown> | null;
  preview: string;
}

/** GetContentVersion payload */
export interface GetContentVersionPayload {
  versionId: string;
}

/** GetContentVersion 返回的完整版本 */
export interface ContentVersionDetail {
  id: string;
  project_id: string;
  content_type: string;
  content: string;
  parent_version_id: string | null;
  created_at: string;
  producer: Record<string, unknown> | null;
}

/** 工作区行（对齐 migrations/0001 workspaces） */
export interface WorkspaceRow {
  id: string;
  name: string;
  root_path?: string;
  created_at?: string;
  archived_at?: string | null;
}

/** CreateWorkspace payload */
export interface CreateWorkspacePayload {
  name: string;
}

/** RenameWorkspace payload */
export interface RenameWorkspacePayload {
  workspaceId: string;
  name: string;
}

/** ArchiveWorkspace payload */
export interface ArchiveWorkspacePayload {
  workspaceId: string;
}

/** ListWorkspaces payload */
export interface ListWorkspacesPayload {
  includeArchived?: boolean;
}

/** 发布平台（PRD-PUB-001 MVP 仅抖音 + 通用） */
export type PublishPlatform = "douyin" | "generic";

/** CreatePlatformVariant payload */
export interface CreatePlatformVariantPayload {
  projectId: string;
  platform: PublishPlatform;
  title: string;
  body: string;
  tags: string[];
  videoVersionId?: string;
}

/** ListPlatformVariants payload */
export interface ListPlatformVariantsPayload {
  projectId: string;
}

/** 平台变体行（对齐 migrations/0003 platform_variants；tags 可能是
 *  JSON string 或已解析数组，展示层用 normalize 处理） */
export interface PlatformVariant {
  id: string;
  platform: string;
  title: string | null;
  body: string | null;
  tags: unknown;
  content_version_id?: string | null;
  validation_status?: string;
  created_at: string;
  updated_at?: string;
}

/** ExportBundle payload */
export interface ExportBundlePayload {
  variantId: string;
}

/** ImportSource payload（Tranche 2：url 直链导入 + 来源记录三字段） */
export interface ImportSourcePayload {
  /** 本地导入：绝对路径（与 url 二选一） */
  local_uri?: string;
  /** 链接导入：https 直链（与 local_uri 二选一） */
  url?: string;
  kind?: string;
  content_hash?: string;
  /** 来源记录（PRD-SRC-003） */
  original_url?: string | null;
  author?: string | null;
  rights_declaration?: "original" | "licensed" | "reference" | null;
  metadata?: Record<string, unknown>;
}
