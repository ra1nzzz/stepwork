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
