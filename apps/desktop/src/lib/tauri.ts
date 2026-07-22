/**
 * Tauri API 封装
 * - 在 Tauri 环境中走 @tauri-apps/api invoke
 * - 在纯浏览器环境（npm run dev）下返回 mock 数据，便于前端独立调试（P1-UX #1）
 */

import type { AppInfo, HealthStatus } from "./types";

export const isTauri = (): boolean =>
  typeof window !== "undefined" && "__TAURI__" in window;

type InvokeFn = <T>(cmd: string, args?: Record<string, unknown>) => Promise<T>;

let cachedInvoke: InvokeFn | null = null;

async function getInvoke(): Promise<InvokeFn> {
  if (cachedInvoke) return cachedInvoke;
  const mod = await import("@tauri-apps/api/core");
  cachedInvoke = mod.invoke as InvokeFn;
  return cachedInvoke;
}

const MOCK_STARTED_AT = Date.now();

function mockHealth(): HealthStatus {
  const uptime = Math.floor((Date.now() - MOCK_STARTED_AT) / 1000);
  return {
    status: "ok",
    version: "0.1.0-mock",
    protocol_version: "1",
    uptime_seconds: uptime,
    pid: 42424,
    last_heartbeat_at: new Date().toISOString(),
    startup_duration_ms: 832,
    active_jobs: 0,
    degraded_reasons: [],
    runtime_info: {
      python_version: "3.13.0 (mock)",
      sqlite_version: "3.45.1",
      platform: "browser-mock",
    },
  };
}

function mockAppInfo(): AppInfo {
  return {
    version: "0.1.0-mock",
    platform: "browser",
    stepwork_home: "~/STEPWORK (mock)",
    restart_count: 0,
    last_crash_at: null,
  };
}

export async function getWorkerHealth(): Promise<HealthStatus> {
  if (!isTauri()) {
    // 模拟网络/进程延迟
    await new Promise((r) => setTimeout(r, 220));
    return mockHealth();
  }
  const invoke = await getInvoke();
  return invoke<HealthStatus>("get_worker_health");
}

export async function restartWorker(): Promise<void> {
  if (!isTauri()) {
    await new Promise((r) => setTimeout(r, 800));
    return;
  }
  const invoke = await getInvoke();
  return invoke<void>("restart_worker");
}

export async function getAppInfo(): Promise<AppInfo> {
  if (!isTauri()) {
    return mockAppInfo();
  }
  const invoke = await getInvoke();
  return invoke<AppInfo>("get_app_info");
}

/**
 * ===== W3 / W4 Command Bus 桥接（Batch3） =====
 *
 * 所有 W3/W4 命令（ImportSource / TranscribeSource / AnalyzeSource）
 * 都经由 Rust `dispatch_command` 单点转发到 worker 的 Command Bus。
 * 真实 Tauri 环境走 invoke；纯浏览器环境返回 mock 以便独立调试。
 */

import type { CommandEnvelope, CommandResult } from "./types";

/** 浏览器/mock 环境的默认工作区 id */
export const DEFAULT_WORKSPACE_ID = "ws-local";

/** 轻量 uuid（浏览器 crypto.randomUUID 优先，降级到时间+随机） */
function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 构造一个符合 command-envelope.schema.json 的信封 */
export function buildEnvelope(
  commandType: CommandEnvelope["commandType"],
  workspaceId: string,
  projectId: string | null,
  payload: Record<string, unknown>,
): CommandEnvelope {
  return {
    commandId: uuid(),
    commandType,
    schemaVersion: "1.0.0",
    actor: "desktop",
    source: "ui",
    workspaceId,
    projectId,
    idempotencyKey: null,
    payload,
    requestedAt: new Date().toISOString(),
  };
}

/** 转发信封到 worker Command Bus（经 Rust dispatch_command） */
export async function dispatchCommand(
  envelope: CommandEnvelope,
): Promise<CommandResult> {
  if (!isTauri()) {
    await new Promise((r) => setTimeout(r, 300));
    return mockDispatchResult(envelope);
  }
  const invoke = await getInvoke();
  return invoke<CommandResult>("dispatch_command", { envelope });
}

/**
 * 浏览器 mock：导入 / 转写默认成功（无密钥也可演示流程）；
 * 分析默认失败（需要真实密钥），借此暴露失败重试 UI 路径。
 */
function mockDispatchResult(env: CommandEnvelope): CommandResult {
  const ok = env.commandType !== "AnalyzeSource";
  return {
    ok,
    commandId: env.commandId,
    job_id: ok ? `job-${uuid()}` : null,
    artifact_ids: ok ? [`cv-${uuid()}`] : [],
    error: ok ? null : "MOCK_NO_PROVIDER",
    detail: ok ? { mock: true } : null,
  };
}
