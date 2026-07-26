/**
 * 调试模式 Store（W9 增补）
 * - 持有 debug 配置（console / python_window / log_level）
 * - 订阅 Tauri event "worker://log"，缓存最近 N 条日志
 * - console=false 时不订阅 event，不显示控制台
 */

import { create } from "zustand";
import { isTauri } from "@/lib/tauri";

export interface DebugConfig {
  console: boolean;
  python_window: boolean;
  log_level: string;
}

export interface LogEntry {
  line: string;
  ts: string;
  source: string;
}

const MAX_LOGS = 500;

interface DebugStoreState {
  config: DebugConfig;
  logs: LogEntry[];
  isPanelOpen: boolean;
  isLoading: boolean;
  unsubscribe: (() => void) | null;

  init: () => Promise<void>;
  loadConfig: () => Promise<void>;
  updateConfig: (config: Partial<DebugConfig>) => Promise<void>;
  togglePanel: () => void;
  clearLogs: () => void;
  appendLog: (entry: LogEntry) => void;
}

let eventListenerAttached = false;

export const useDebugStore = create<DebugStoreState>((set, get) => ({
  config: { console: true, python_window: false, log_level: "info" },
  logs: [],
  isPanelOpen: false,
  isLoading: false,
  unsubscribe: null,

  init: async () => {
    await get().loadConfig();
  },

  loadConfig: async () => {
    if (!isTauri()) return;
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      const config = await invoke<DebugConfig>("get_debug_config");
      set({ config });

      // 按配置决定是否订阅 event
      if (config.console && !eventListenerAttached) {
        const { listen } = await import("@tauri-apps/api/event");
        const unlisten = await listen<LogEntry>("worker://log", (event) => {
          get().appendLog(event.payload);
        });
        eventListenerAttached = true;
        set({ unsubscribe: unlisten });
      } else if (!config.console && eventListenerAttached) {
        const unsub = get().unsubscribe;
        if (unsub) {
          unsub();
          set({ unsubscribe: null });
          eventListenerAttached = false;
        }
      }
    } catch {
      // 桥未就绪：使用默认配置
    }
  },

  updateConfig: async (partial) => {
    const current = get().config;
    const next = { ...current, ...partial };
    set({ config: next });
    if (!isTauri()) return;
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("set_debug_config", { config: next });
      // 配置变更后重新加载（可能需要重新订阅 event）
      await get().loadConfig();
    } catch {
      // 桥未就绪：仅更新本地状态
    }
  },

  togglePanel: () => {
    set((s) => ({ isPanelOpen: !s.isPanelOpen }));
  },

  clearLogs: () => {
    set({ logs: [] });
  },

  appendLog: (entry) => {
    set((s) => {
      const logs = [...s.logs, entry];
      if (logs.length > MAX_LOGS) {
        logs.splice(0, logs.length - MAX_LOGS);
      }
      return { logs };
    });
  },
}));
