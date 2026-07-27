/**
 * 持久化任务 Store（Tranche 1 / T5）
 * - refresh：dispatch ListJobs 拉取 content_jobs 持久化列表（含应用重启前的任务），
 *   TasksView 挂载期间每 ~2s 轮询一次，持久化列表是任务中心的事实源
 * - applyProgress：应用 worker-notification 的 job.progress 通知，
 *   在两次轮询之间实时推进对应任务行（轮询仍保留作为兜底）
 */

import { create } from "zustand";
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "@/lib/tauri";
import type {
  JobProgressParams,
  JobStage,
  ListJobsPayload,
  PersistedJob,
} from "@/lib/types";

// 当前工作区由 getWorkspaceId() 解析（用户可在设置页切换）
const DEFAULT_LIMIT = 50;

interface JobEventsStoreState {
  jobs: PersistedJob[];
  isLoading: boolean;
  error: string | null;
  /** 最近一次成功刷新时间（ISO），null = 尚未拉取过 */
  lastRefreshAt: string | null;
  refresh: (payload?: ListJobsPayload) => Promise<void>;
  applyProgress: (p: JobProgressParams) => void;
}

export const useJobEventsStore = create<JobEventsStoreState>((set, get) => ({
  jobs: [],
  isLoading: false,
  error: null,
  lastRefreshAt: null,

  refresh: async (payload) => {
    // 轮询期间避免请求堆叠
    if (get().isLoading) return;
    set({ isLoading: true });
    try {
      const env = buildEnvelope("ListJobs", getWorkspaceId(), null, {
        limit: DEFAULT_LIMIT,
        ...(payload ?? {}),
      });
      const res = await dispatchCommand(env);
      if (!res.ok) {
        set({ error: res.error ?? "LIST_JOBS_FAILED" });
        return;
      }
      const detail = (res.detail ?? {}) as { jobs?: PersistedJob[] };
      set({
        jobs: detail.jobs ?? [],
        error: null,
        lastRefreshAt: new Date().toISOString(),
      });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    } finally {
      set({ isLoading: false });
    }
  },

  applyProgress: (p) => {
    const jobs = get().jobs;
    const now = new Date().toISOString();
    const existing = jobs.find((j) => j.id === p.job_id);
    if (existing) {
      set({
        jobs: jobs.map((j) =>
          j.id === p.job_id
            ? {
                ...j,
                state: p.state,
                stage: (p.stage as JobStage | null) ?? j.stage,
                progress: p.progress,
                error_code: p.error_code,
                updated_at: now,
              }
            : j,
        ),
      });
      return;
    }
    // 新任务（尚未被轮询捕获）：插到列表头部，下一次 ListJobs 会补全其余字段
    const fresh: PersistedJob = {
      id: p.job_id,
      job_type: p.job_type,
      state: p.state,
      stage: (p.stage as JobStage | null) ?? null,
      progress: p.progress,
      attempt_count: 0,
      error_code: p.error_code,
      created_at: now,
      updated_at: now,
    };
    set({ jobs: [fresh, ...jobs] });
  },
}));
