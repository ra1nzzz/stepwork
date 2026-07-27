/**
 * worker-notification 事件订阅（Tranche 1 / T1）
 *
 * 链路：Python worker（notify 回调）→ Rust RpcClient（app.emit）→ Tauri 事件
 * 'worker-notification'，payload = { method, params }。
 *
 * App 顶层调用一次 useWorkerNotifications()：
 * - method == 'job.progress' 时把 params 分发给相关 store：
 *   - useJobEventsStore：任务中心持久化列表的实时行更新
 *   - useRenderStore：渲染进行中的进度条
 *   - useTranscriptStore：转写任务条目
 * - 非 Tauri 环境（浏览器 / dev_bridge）无事件桥，直接跳过（轮询兜底）。
 */

import { useEffect } from "react";
import { isTauri } from "@/lib/tauri";
import { useRenderStore } from "@/stores/useRenderStore";
import { useTranscriptStore } from "@/stores/useTranscriptStore";
import { useJobEventsStore } from "@/stores/useJobEventsStore";
import type { JobProgressParams, WorkerNotification } from "@/lib/types";

function handleNotification(payload: WorkerNotification | undefined): void {
  if (!payload || payload.method !== "job.progress") return;
  const p = payload.params as unknown as JobProgressParams;
  if (!p || typeof p.job_id !== "string") return;
  useJobEventsStore.getState().applyProgress(p);
  useRenderStore.getState().applyJobProgress(p);
  useTranscriptStore.getState().applyJobProgress(p);
}

/** App 顶层调用一次；组件卸载时自动 unlisten */
export function useWorkerNotifications(): void {
  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | null = null;
    let disposed = false;
    void (async () => {
      try {
        const { listen } = await import("@tauri-apps/api/event");
        const stop = await listen<WorkerNotification>(
          "worker-notification",
          (event) => handleNotification(event.payload),
        );
        // effect 已清理（严格模式双挂载等）时立即释放
        if (disposed) stop();
        else unlisten = stop;
      } catch {
        // 事件桥不可用时静默：轮询仍是兜底
      }
    })();
    return () => {
      disposed = true;
      if (unlisten) unlisten();
    };
  }, []);
}
