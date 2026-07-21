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
