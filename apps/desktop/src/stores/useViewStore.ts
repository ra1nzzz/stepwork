/**
 * 视图路由 Store（W3-W4 Batch3）
 * 驱动 Sidebar 导航与 App 主区切换；默认停在 home（引擎概览）。
 */

import { create } from "zustand";

export type ViewId =
  | "home"
  | "import"
  | "transcript"
  | "analysis"
  | "script"
  | "render"
  | "settings"
  | "provenance"
  | "agent"
  | "diagnostics"
  | "plugins";

interface ViewStoreState {
  currentView: ViewId;
  setView: (v: ViewId) => void;
}

export const useViewStore = create<ViewStoreState>((set) => ({
  currentView: "home",
  setView: (v) => set({ currentView: v }),
}));
