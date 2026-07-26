/**
 * Tauri API 封装
 * - Tauri 环境：走 @tauri-apps/api invoke → Rust sidecar → Python worker
 * - 浏览器环境：自动探测 dev_bridge（VITE_DEV_BRIDGE=1 或自动探测）
 * - 无连接时：返回明确的错误状态，绝不返回虚假 mock 数据
 */

import type {
  AppInfo,
  HealthStatus,
  CommandEnvelope,
  CommandResult,
  ConfigResult,
} from "./types";
import { useSettingsStore } from "@/stores/useSettingsStore";
import type { SettingsConfig } from "@/stores/useSettingsStore";

export const isTauri = (): boolean =>
  typeof window !== "undefined" &&
  ("__TAURI_INTERNALS__" in window || "__TAURI__" in window);

/* ===== Dev Bridge 自动探测 ===== */

const DEV_BRIDGE_EXPLICIT = import.meta.env.VITE_DEV_BRIDGE === "1";
const DEV_BRIDGE_URL =
  (import.meta.env.VITE_DEV_BRIDGE_URL as string | undefined) ??
  "http://127.0.0.1:8787";

let _bridgeDetected: boolean | null = null;

/** 探测 dev_bridge 是否可用（缓存结果，避免重复探测） */
async function isBridgeAvailable(): Promise<boolean> {
  if (_bridgeDetected !== null) return _bridgeDetected;
  if (DEV_BRIDGE_EXPLICIT) {
    _bridgeDetected = true;
    return true;
  }
  try {
    const res = await fetch(`${DEV_BRIDGE_URL}/health`, {
      method: "GET",
      signal: AbortSignal.timeout(1200),
    });
    _bridgeDetected = res.ok;
    return _bridgeDetected;
  } catch {
    _bridgeDetected = false;
    return false;
  }
}

/** 重置 bridge 探测缓存（用于手动重连场景） */
export function resetBridgeDetection(): void {
  _bridgeDetected = null;
}

type InvokeFn = <T>(cmd: string, args?: Record<string, unknown>) => Promise<T>;

let cachedInvoke: InvokeFn | null = null;

async function getInvoke(): Promise<InvokeFn> {
  if (cachedInvoke) return cachedInvoke;
  const mod = await import("@tauri-apps/api/core");
  cachedInvoke = mod.invoke as InvokeFn;
  return cachedInvoke;
}

/** 构造未连接后端的健康状态 */
function disconnectedHealth(): HealthStatus {
  return {
    status: "down",
    version: "0.0.0",
    protocol_version: "1",
    uptime_seconds: 0,
    pid: 0,
    last_heartbeat_at: null,
    startup_duration_ms: 0,
    active_jobs: 0,
    degraded_reasons: ["backend_not_connected"],
    runtime_info: {
      python_version: "n/a",
      sqlite_version: "n/a",
      platform: "browser",
    },
  };
}

/** 构造未连接后端的 AppInfo */
function disconnectedAppInfo(): AppInfo {
  return {
    version: "0.0.0",
    platform: "browser",
    stepwork_home: "未连接",
    restart_count: 0,
    last_crash_at: null,
  };
}

/* ===== Health / AppInfo ===== */

export async function getWorkerHealth(): Promise<HealthStatus> {
  if (isTauri()) {
    const invoke = await getInvoke();
    return invoke<HealthStatus>("get_worker_health");
  }
  if (await isBridgeAvailable()) {
    try {
      const res = await fetch(`${DEV_BRIDGE_URL}/health`);
      if (res.ok) return (await res.json()) as HealthStatus;
    } catch {
      /* bridge 探测后仍失败 */
    }
  }
  return disconnectedHealth();
}

export async function restartWorker(): Promise<void> {
  if (isTauri()) {
    const invoke = await getInvoke();
    return invoke<void>("restart_worker");
  }
  if (await isBridgeAvailable()) {
    // dev_bridge 无重启接口，静默
    return;
  }
  throw new Error("未连接到后端，无法重启 Worker");
}

export async function getAppInfo(): Promise<AppInfo> {
  if (isTauri()) {
    const invoke = await getInvoke();
    return invoke<AppInfo>("get_app_info");
  }
  if (await isBridgeAvailable()) {
    // dev_bridge 无 app_info 接口，返回降级信息
    return {
      version: "0.1.0-devbridge",
      platform: "browser",
      stepwork_home: `dev-bridge (${DEV_BRIDGE_URL})`,
      restart_count: 0,
      last_crash_at: null,
    };
  }
  return disconnectedAppInfo();
}

/* ===== Command Bus 桥接 ===== */

/** 浏览器环境的默认工作区 id */
export const DEFAULT_WORKSPACE_ID = "ws-local";

export function getWorkspaceId(): string {
  return DEFAULT_WORKSPACE_ID;
}

function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function buildEnvelope<T>(
  commandType: CommandEnvelope["commandType"],
  workspaceId: string,
  projectId: string | null,
  payload: T,
): CommandEnvelope {
  return {
    commandId: uuid(),
    commandType,
    schemaVersion: "1",
    actor: { type: "desktop", id: "ui" },
    source: "ui",
    workspaceId,
    projectId,
    idempotencyKey: null,
    payload: payload as unknown as Record<string, unknown>,
    requestedAt: new Date().toISOString(),
  };
}

/** 未连接后端的统一错误返回 */
function disconnectedResult(envelope: CommandEnvelope): CommandResult {
  return {
    ok: false,
    commandId: envelope.commandId,
    job_id: null,
    artifact_ids: [],
    error: "BACKEND_NOT_CONNECTED",
    detail: {
      message:
        "未连接到后端。请在 Tauri 桌面环境中运行，或启动 dev_bridge（python worker/dev_bridge.py）。",
    },
  };
}

export async function dispatchCommand(
  envelope: CommandEnvelope,
): Promise<CommandResult> {
  if (isTauri()) {
    const invoke = await getInvoke();
    return invoke<CommandResult>("dispatch_command", { envelope });
  }
  if (await isBridgeAvailable()) {
    try {
      const res = await fetch(`${DEV_BRIDGE_URL}/dispatch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(envelope),
      });
      if (!res.ok) {
        const txt = await res.text();
        return {
          ok: false,
          commandId: envelope.commandId,
          job_id: null,
          artifact_ids: [],
          error: `DEV_BRIDGE_HTTP_${res.status}`,
          detail: { http_body: txt },
        };
      }
      return (await res.json()) as CommandResult;
    } catch (e) {
      return {
        ok: false,
        commandId: envelope.commandId,
        job_id: null,
        artifact_ids: [],
        error: "DEV_BRIDGE_OFFLINE",
        detail: { cause: String(e) },
      };
    }
  }
  return disconnectedResult(envelope);
}

/* ===== Config 桥接 ===== */

/** 本地推导解析摘要（bridge 未启用时给 SettingsView 降级展示） */
function deriveResolved(s: SettingsConfig): ConfigResult["resolved"] {
  return {
    ai: { provider: s.llm.provider, model: s.llm.model, hasKey: !!s.llm.apiKey },
    asr: { provider: s.asr.provider, hasKey: !!s.asr.apiKey },
    tts: { provider: s.tts.provider, model: s.tts.model, hasKey: !!s.tts.apiKey },
  };
}

function adaptConfig(res: CommandResult): ConfigResult {
  if (!res.ok) return { ok: false, error: res.error ?? "bridge_error" };
  const detail = (res.detail ?? {}) as {
    config?: Record<string, unknown>;
    resolved?: ConfigResult["resolved"];
  };
  return { ok: true, config: detail.config, resolved: detail.resolved };
}

async function bridgeConfig(
  commandType: CommandEnvelope["commandType"],
  payload: Record<string, unknown>,
): Promise<ConfigResult> {
  const env = buildEnvelope(commandType, getWorkspaceId(), null, payload);
  const res = await dispatchCommand(env);
  return adaptConfig(res);
}

export async function getConfig(): Promise<ConfigResult> {
  if (isTauri()) {
    const env = buildEnvelope("GetConfig", getWorkspaceId(), null, {});
    const invoke = await getInvoke();
    return adaptConfig(await invoke<CommandResult>("dispatch_command", { envelope: env }));
  }
  if (await isBridgeAvailable()) {
    return bridgeConfig("GetConfig", {});
  }
  // 未连接后端时，返回本地 store 作为降级（但标记为未持久化到后端）
  const s = useSettingsStore.getState().settings;
  return {
    ok: true,
    config: s as unknown as Record<string, unknown>,
    resolved: deriveResolved(s),
  };
}

export async function updateConfig(settings: SettingsConfig): Promise<ConfigResult> {
  const payload = settings as unknown as Record<string, unknown>;
  if (isTauri()) {
    const env = buildEnvelope("UpdateConfig", getWorkspaceId(), null, payload);
    const invoke = await getInvoke();
    return adaptConfig(await invoke<CommandResult>("dispatch_command", { envelope: env }));
  }
  if (await isBridgeAvailable()) {
    return bridgeConfig("UpdateConfig", payload);
  }
  // 未连接后端时，仅更新本地内存 store
  useSettingsStore.getState().update({ ...settings });
  return { ok: true };
}
