/**
 * 转写 Store（W3 Batch3）
 * - 对素材 dispatch TranscribeSource，跟踪 job 阶段 / 进度 / 失败
 * - 支持取消（本地标记）与失败重试（PRD §338）
 *
 * 注：worker 端转写为同步执行（命令内 await ASR），故无实时进度流；
 * 真实环境由 content_version 回填正文，mock 环境仅保留状态与失败重试路径。
 */

import { create } from "zustand";
import { buildEnvelope, dispatchCommand } from "@/lib/tauri";
import type { TranscriptSegment } from "@/lib/types";

const WORKSPACE = "ws-local";

export type TranscriptStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface TranscriptJob {
  id: string;
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
  cancel: (id: string) => void;
  retry: (id: string) => Promise<void>;
  reset: () => void;
}

function newJob(assetId: string | null, opts?: Record<string, unknown>): TranscriptJob {
  return {
    id: `tj-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
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
      const env = buildEnvelope("TranscribeSource", WORKSPACE, null, {
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

  cancel: (id) => {
    set({
      jobs: patch(get().jobs, id, { status: "cancelled", progress: 0 }),
    });
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
