/**
 * 视频草稿渲染 Store（W6 Batch1）
 * - 对 script/transcript 版 dispatch CreateRenderJob → 渲染视频草稿
 * - 支持取消（CancelJob，置位后端 cancel event）与失败重试（PRD §338）
 * - 复用既有 buildEnvelope / dispatchCommand；浏览器下由 tauri.ts mock 返回示例产物
 */

import { create } from "zustand";
import { buildEnvelope, dispatchCommand } from "@/lib/tauri";
import type {
  VideoDraftMeta,
  CreateRenderJobPayload,
  CancelJobPayload,
} from "@/lib/types";

const WORKSPACE = "ws-local";

export type RenderStatus =
  | "idle"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

interface RenderStoreState {
  /** 渲染源版本 id（默认 mock 的 script 版，便于演示） */
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
  reset: () => void;
}

function metaFromDetail(
  detail: Record<string, unknown>,
  template: string,
  ttsEngine: string,
  sourceVersionId: string,
): VideoDraftMeta {
  return {
    video_uri: (detail.video_uri as string | undefined) ?? "",
    duration_seconds: 0,
    template: (detail.template as string | undefined) ?? template,
    tts_engine: (detail.tts_engine as string | undefined) ?? ttsEngine,
    resolution: [1080, 1920],
    fps: 30,
    source_version_id: sourceVersionId,
  };
}

export const useRenderStore = create<RenderStoreState>((set, get) => ({
  sourceVersionId: "cv-script-local",
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
        null,
        payload as unknown as Record<string, unknown>,
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
        jobId: res.job_id,
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
    const id = get().jobId;
    if (!id) return;
    const payload: CancelJobPayload = { job_id: id };
    const env = buildEnvelope(
      "CancelJob",
      WORKSPACE,
      null,
      payload as unknown as Record<string, unknown>,
    );
    try {
      await dispatchCommand(env);
      set({ status: "cancelled" });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      set({ error: msg });
    }
  },

  retry: async () => {
    await get().render();
  },

  reset: () =>
    set({
      status: "idle",
      jobId: null,
      progress: 0,
      draft: null,
      error: null,
    }),
}));
