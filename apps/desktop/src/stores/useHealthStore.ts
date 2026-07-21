/**
 * 健康状态 Zustand Store（P1-UX-2）
 * - 指数退避：ok→5000ms / degraded→3000ms / down→1000ms 起步、30000ms 封顶
 * - document.visibilitychange：hidden 暂停、visible 恢复
 */

import { create } from "zustand";
import { getWorkerHealth, restartWorker } from "@/lib/tauri";
import type { HealthStatus, SidecarError } from "@/lib/types";

const BASE_INTERVAL_OK = 5000;
const BASE_INTERVAL_DEGRADED = 3000;
const BASE_INTERVAL_DOWN = 1000;
const MAX_INTERVAL = 30000;

function baseIntervalFor(status: HealthStatus["status"] | null): number {
  if (status === "ok") return BASE_INTERVAL_OK;
  if (status === "degraded") return BASE_INTERVAL_DEGRADED;
  return BASE_INTERVAL_DOWN;
}

interface HealthStoreState {
  health: HealthStatus | null;
  error: SidecarError | null;
  isLoading: boolean;
  pollingInterval: number;
  pollingTimer: ReturnType<typeof setTimeout> | null;
  consecutiveFailures: number;
  isPolling: boolean;

  startPolling: () => void;
  stopPolling: () => void;
  fetchHealth: () => Promise<void>;
  restart: () => Promise<void>;
}

function toSidecarError(err: unknown): SidecarError {
  if (err && typeof err === "object") {
    const e = err as Record<string, unknown>;
    const kind = typeof e.kind === "string" ? e.kind : "Unknown";
    return {
      kind: kind as SidecarError["kind"],
      code: typeof e.code === "string" ? e.code : "UNKNOWN",
      message: typeof e.message === "string" ? e.message : String(err),
      retryable: typeof e.retryable === "boolean" ? e.retryable : true,
      details:
        e.details && typeof e.details === "object"
          ? (e.details as Record<string, unknown>)
          : null,
      correlation_id:
        typeof e.correlation_id === "string" ? e.correlation_id : null,
    };
  }
  return {
    kind: "Unknown",
    code: "UNKNOWN",
    message: err instanceof Error ? err.message : String(err),
    retryable: true,
    details: null,
    correlation_id: null,
  };
}

export const useHealthStore = create<HealthStoreState>((set, get) => {
  let visibilityHandler: (() => void) | null = null;

  const scheduleNext = () => {
    const state = get();
    if (!state.isPolling) return;
    if (state.pollingTimer) {
      clearTimeout(state.pollingTimer);
    }
    const timer = setTimeout(() => {
      void get().fetchHealth();
    }, state.pollingInterval);
    set({ pollingTimer: timer });
  };

  const attachVisibilityListener = () => {
    if (typeof document === "undefined" || visibilityHandler) return;
    visibilityHandler = () => {
      const state = get();
      if (!state.isPolling) return;
      if (document.visibilityState === "hidden") {
        if (state.pollingTimer) {
          clearTimeout(state.pollingTimer);
          set({ pollingTimer: null });
        }
      } else if (document.visibilityState === "visible") {
        void get().fetchHealth();
      }
    };
    document.addEventListener("visibilitychange", visibilityHandler);
  };

  const detachVisibilityListener = () => {
    if (typeof document === "undefined" || !visibilityHandler) return;
    document.removeEventListener("visibilitychange", visibilityHandler);
    visibilityHandler = null;
  };

  return {
    health: null,
    error: null,
    isLoading: true,
    pollingInterval: BASE_INTERVAL_DOWN,
    pollingTimer: null,
    consecutiveFailures: 0,
    isPolling: false,

    startPolling: () => {
      const state = get();
      if (state.isPolling) return;
      set({ isPolling: true });
      attachVisibilityListener();
      void get().fetchHealth();
    },

    stopPolling: () => {
      const state = get();
      if (state.pollingTimer) {
        clearTimeout(state.pollingTimer);
      }
      detachVisibilityListener();
      set({ isPolling: false, pollingTimer: null });
    },

    fetchHealth: async () => {
      try {
        const health = await getWorkerHealth();
        const base = baseIntervalFor(health.status);
        set({
          health,
          error: null,
          isLoading: false,
          consecutiveFailures: 0,
          pollingInterval: base,
        });
      } catch (err) {
        const sidecarError = toSidecarError(err);
        const failures = get().consecutiveFailures + 1;
        const base = baseIntervalFor(get().health?.status ?? null);
        const backoff = Math.min(base * 2 ** (failures - 1), MAX_INTERVAL);
        set({
          error: sidecarError,
          isLoading: false,
          consecutiveFailures: failures,
          pollingInterval: backoff,
        });
      } finally {
        scheduleNext();
      }
    },

    restart: async () => {
      await restartWorker();
      // 重启后立刻拉一次，重置退避
      set({ consecutiveFailures: 0, pollingInterval: BASE_INTERVAL_DOWN });
      await get().fetchHealth();
    },
  };
});
