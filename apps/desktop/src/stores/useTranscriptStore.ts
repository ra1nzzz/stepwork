/**
 * 转写 Store（W3 Batch3）
 * - 对素材 dispatch TranscribeSource，跟踪 job 阶段 / 进度 / 失败
 * - 支持取消（dispatch CancelJob + 本地标记）与失败重试（PRD §338）
 *
 * 注：worker 端转写为同步执行（命令内 await ASR），故无实时进度流；
 * 真实环境由 content_version 回填正文。cancel 仍会调 CancelJob 通知后端
 * 把 content_jobs 记录标记为 cancelled。
 */

import { create } from "zustand";
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "@/lib/tauri";
import { useViewStore } from "@/stores/useViewStore";
import type { JobProgressParams, TranscriptSegment } from "@/lib/types";

// 当前工作区由 getWorkspaceId() 解析（用户可在设置页切换）

/** 当前选中项目 id（envelope.projectId）；确实没有时才回落 null */
const currentProjectId = (): string | null =>
  useViewStore.getState().selectedProjectId ?? null;

export type TranscriptStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface TranscriptJob {
  id: string;
  /** 后端 content_jobs.id（用于 CancelJob）；可能为 null（后端未返回时） */
  jobId: string | null;
  assetId: string | null;
  versionId: string | null;
  language: string | null;
  status: TranscriptStatus;
  progress: number;
  text: string;
  segments: TranscriptSegment[];
  error: string | null;
  createdAt: string;
}

interface TranscriptStoreState {
  jobs: TranscriptJob[];
  isBusy: boolean;
  error: string | null;
  transcribe: (assetId: string, opts?: Record<string, unknown>) => Promise<void>;
  cancel: (id: string) => Promise<void>;
  retry: (id: string) => Promise<void>;
  /** 应用后端 job.progress 通知（按后端 jobId 匹配本地任务条目） */
  applyJobProgress: (p: JobProgressParams) => void;
  reset: () => void;
}

function newJob(assetId: string | null, opts?: Record<string, unknown>): TranscriptJob {
  return {
    id: `tj-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    jobId: null,
    assetId,
    versionId: null,
    language: (opts?.language as string | undefined) ?? null,
    status: "pending",
    progress: 0,
    text: "",
    segments: [],
    error: null,
    createdAt: new Date().toISOString(),
  };
}

export const useTranscriptStore = create<TranscriptStoreState>((set, get) => ({
  jobs: [],
  isBusy: false,
  error: null,

  transcribe: async (assetId, opts) => {
    const job = newJob(assetId, opts);
    set({ jobs: [...get().jobs, job], isBusy: true, error: null });
    try {
      set({ jobs: patch(get().jobs, job.id, { status: "running", progress: 0.1 }) });
      const env = buildEnvelope("TranscribeSource", getWorkspaceId(), currentProjectId(), {
        asset_id: assetId,
        opts: opts ?? {},
      });
      const res = await dispatchCommand(env);
      if (!res.ok) {
        throw new Error(res.error ?? "TRANSCRIBE_FAILED");
      }
      const detail = (res.detail ?? {}) as Record<string, unknown>;
      const language = (detail.language as string | undefined) ?? null;
      set({
        jobs: patch(get().jobs, job.id, {
          status: "succeeded",
          progress: 1,
          versionId: res.artifact_ids[0] ?? null,
          jobId: res.job_id ?? null,
          language,
        }),
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      set({
        jobs: patch(get().jobs, job.id, { status: "failed", error: msg }),
        error: msg,
      });
    } finally {
      set({ isBusy: false });
    }
  },

  cancel: async (id) => {
    const job = get().jobs.find((j) => j.id === id);
    if (!job) return;
    // 本地立即标记 cancelled
    set({ jobs: patch(get().jobs, id, { status: "cancelled", progress: 0 }) });
    // 通知后端 CancelJob（如果有 jobId）
    if (job.jobId) {
      try {
        const env = buildEnvelope("CancelJob", getWorkspaceId(), currentProjectId(), {
          job_id: job.jobId,
        });
        await dispatchCommand(env);
      } catch {
        // 静默：本地已标记 cancelled，后端失败不回滚
      }
    }
  },

  applyJobProgress: (p) => {
    const job = get().jobs.find((j) => j.jobId === p.job_id);
    if (!job) return;
    // 终态由 dispatch 返回值裁决；通知只推进 running 中的进度与失败态
    const changes: Partial<TranscriptJob> = { progress: p.progress };
    if (p.state === "running") changes.status = "running";
    else if (p.state === "succeeded") changes.status = "succeeded";
    else if (p.state === "failed") {
      changes.status = "failed";
      changes.error = p.error_code ?? job.error;
    } else if (p.state === "cancelled") changes.status = "cancelled";
    set({ jobs: patch(get().jobs, job.id, changes) });
  },

  retry: async (id) => {
    const job = get().jobs.find((j) => j.id === id);
    if (!job || !job.assetId) return;
    // 复用原 assetId + 语言偏好重试
    const opts: Record<string, unknown> = {};
    if (job.language) opts.language = job.language;
    await get().transcribe(job.assetId, opts);
  },

  reset: () => set({ jobs: [], error: null }),
}));

function patch(
  jobs: TranscriptJob[],
  id: string,
  changes: Partial<TranscriptJob>,
): TranscriptJob[] {
  return jobs.map((j) => (j.id === id ? { ...j, ...changes } : j));
}
