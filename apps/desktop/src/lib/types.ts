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
  | "PENDING"
  | "LEASED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED"
  | "EXPIRED";

export type JobStage =
  | "PENDING"
  | "IMPORTING"
  | "TRANSCRIBING"
  | "ANALYZING"
  | "DONE";

/** Command Bus 信封（对齐 schemas/command-envelope.schema.json） */
export interface CommandEnvelope {
  commandId: string;
  commandType: "ImportSource" | "TranscribeSource" | "AnalyzeSource";
  schemaVersion: string;
  actor: string;
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

export interface AnalysisTopic {
  title: string;
  summary: string;
  timestamp?: number | null;
}

export interface AnalysisChapter {
  title: string;
  start: number;
  end: number;
}

export interface AnalysisReport {
  status: AnalysisStatus;
  summary: string;
  chapters: AnalysisChapter[];
  topics: AnalysisTopic[];
  sentiment: string | null;
  provider: string | null;
  model: string | null;
  confidence: number | null;
  created_at: string | null;
  error: string | null;
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
