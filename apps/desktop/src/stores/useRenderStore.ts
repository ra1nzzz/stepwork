/**
 * 视频草稿渲染 Store（W6 Batch1）
 * - 对 script/transcript 版 dispatch CreateRenderJob → 渲染视频草稿
 * - 支持取消（CancelJob，置位后端 cancel event）与失败重试（PRD §338）
 * - 复用既有 buildEnvelope / dispatchCommand
 *
 * 修复：
 * - cancel 在 jobId 为 null（render 调用中尚未返回）时也清本地 isBusy + 置 cancelled
 * - retry 前清旧 error，避免 UI 残留上一次失败文案
 * - metaFromDetail 优先读后端 detail 中的 resolution/fps/duration_seconds
 */

import { create } from "zustand";
import { buildEnvelope, dispatchCommand } from "@/lib/tauri";
import { useViewStore } from "@/stores/useViewStore";
import type {
  VideoDraftMeta,
  CreateRenderJobPayload,
  CancelJobPayload,
  JobProgressParams,
} from "@/lib/types";

const WORKSPACE = "ws-local";

/** 当前选中项目 id（envelope.projectId）；确实没有时才回落 null */
const currentProjectId = (): string | null =>
  useViewStore.getState().selectedProjectId ?? null;

export type RenderStatus =
  | "idle"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

interface RenderStoreState {
  /** 渲染源版本 id */
  sourceVersionId: string;
  template: string;
  ttsEngine: "synthesize" | "user_audio";
  status: RenderStatus;
  jobId: string | null;
  progress: number;
  draft: VideoDraftMeta | null;
  isBusy: boolean;
  error: string | null;

  setSourceVersion: (id: string) => void;
  setTemplate: (t: string) => void;
  setTtsEngine: (e: "synthesize" | "user_audio") => void;
  render: () => Promise<void>;
  cancel: () => Promise<void>;
  retry: () => Promise<void>;
  /** 应用后端 job.progress 通知（渲染进行中时实时推进进度条） */
  applyJobProgress: (p: JobProgressParams) => void;
  reset: () => void;
}

function metaFromDetail(
  detail: Record<string, unknown>,
  template: string,
  ttsEngine: string,
  sourceVersionId: string,
): VideoDraftMeta {
  // 优先读后端真实值；缺失时回落到默认（竖屏 9:16）
  const resolution = (detail.resolution as [number, number] | undefined) ?? [1080, 1920];
  const fps = (detail.fps as number | undefined) ?? 30;
  const durationSeconds = (detail.duration_seconds as number | undefined) ?? 0;
  return {
    video_uri: (detail.video_uri as string | undefined) ?? "",
    duration_seconds: durationSeconds,
    template: (detail.template as string | undefined) ?? template,
    tts_engine: (detail.tts_engine as string | undefined) ?? ttsEngine,
    resolution,
    fps,
    source_version_id: sourceVersionId,
  };
}

export const useRenderStore = create<RenderStoreState>((set, get) => ({
  sourceVersionId: "",
  template: "vertical-caption-v1",
  ttsEngine: "synthesize",
  status: "idle",
  jobId: null,
  progress: 0,
  draft: null,
  isBusy: false,
  error: null,

  setSourceVersion: (id) => set({ sourceVersionId: id }),
  setTemplate: (t) => set({ template: t }),
  setTtsEngine: (e) => set({ ttsEngine: e }),

  render: async () => {
    if (get().isBusy) return;
    set({ status: "running", isBusy: true, error: null, progress: 0.05 });
    try {
      const payload: CreateRenderJobPayload = {
        source_version_id: get().sourceVersionId,
        template: get().template,
        tts_engine: get().ttsEngine,
      };
      const env = buildEnvelope(
        "CreateRenderJob",
        WORKSPACE,
        currentProjectId(),
        payload,
      );
      const res = await dispatchCommand(env);
      if (!res.ok) {
        const err = res.error ?? "RENDER_FAILED";
        if (err.includes("CANCELLED")) {
          set({ status: "cancelled", error: err });
        } else {
          set({ status: "failed", error: err });
        }
        return;
      }
      const detail = (res.detail ?? {}) as Record<string, unknown>;
      set({
        status: "succeeded",
        jobId: res.job_id ?? null,
        progress: 1,
        draft: metaFromDetail(
          detail,
          get().template,
          get().ttsEngine,
          get().sourceVersionId,
        ),
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      set({ status: "failed", error: msg });
    } finally {
      set({ isBusy: false });
    }
  },

  cancel: async () => {
    // 无论 jobId 是否存在，都先清本地状态（避免 jobId null 时 UI 卡在"渲染中"）
    set({ status: "cancelled", isBusy: false });
    const id = get().jobId;
    if (!id) return;
    const payload: CancelJobPayload = { job_id: id };
    const env = buildEnvelope(
      "CancelJob",
      WORKSPACE,
      currentProjectId(),
      payload,
    );
    try {
      await dispatchCommand(env);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      set({ error: msg });
    }
  },

  applyJobProgress: (p) => {
    if (p.job_type !== "render") return;
    const s = get();
    // dispatch 返回前 jobId 为 null：渲染中的通知按 job_type 归属当前活动任务
    const isActive = s.jobId === p.job_id || (s.jobId === null && s.status === "running");
    if (!isActive) return;
    const changes: Partial<RenderStoreState> = {
      jobId: p.job_id,
      progress: p.progress,
    };
    // 终态（draft 等详情）仍由 dispatch 返回值裁决；通知只推进进度与失败/取消态
    if (p.state === "failed") {
      changes.status = "failed";
      changes.error = p.error_code ?? s.error;
    } else if (p.state === "cancelled") {
      changes.status = "cancelled";
    }
    set(changes);
  },

  retry: async () => {
    // 清旧 error，避免 UI 残留上一次失败文案
    set({ error: null, status: "idle", draft: null, jobId: null, progress: 0 });
    await get().render();
  },

  reset: () =>
    set({
      status: "idle",
      jobId: null,
      progress: 0,
      draft: null,
      error: null,
      isBusy: false,
    }),
}));
