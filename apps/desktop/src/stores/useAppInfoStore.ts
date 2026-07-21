/**
 * 应用信息 Store（W2 carry-over #5）
 * 启动时拉取一次 Tauri ``get_app_info``，供 Footer 展示版本 / 平台 /
 * stepwork_home / sidecar 自愈重启次数 / 最近崩溃时间。
 *
 * 与 useHealthStore 解耦：app 元信息只读、基本恒定，无需轮询。
 */

import { create } from "zustand";
import { getAppInfo } from "@/lib/tauri";
import type { AppInfo } from "@/lib/types";

interface AppInfoStoreState {
  appInfo: AppInfo | null;
  isLoading: boolean;
  fetchAppInfo: () => Promise<void>;
}

export const useAppInfoStore = create<AppInfoStoreState>((set) => ({
  appInfo: null,
  isLoading: true,

  fetchAppInfo: async () => {
    try {
      const appInfo = await getAppInfo();
      set({ appInfo, isLoading: false });
    } catch {
      // 拉取失败不影响主流程：Footer 以占位态渲染
      set({ isLoading: false });
    }
  },
}));
