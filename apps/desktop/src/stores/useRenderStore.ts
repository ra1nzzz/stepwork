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
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "@/lib/tauri";
import { useViewStore } from "@/stores/useViewStore";
import type {
  VideoDraftMeta,
  CreateRenderJobPayload,
  CancelJobPayload,
  JobProgressParams,
  RenderArtifacts,
  ProviderInvocation,
} from "@/lib/types";

// 当前工作区由 getWorkspaceId() 解析（用户可在设置页切换）

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
  /** PRD-REN-005：画幅比例（9:16 / 16:9 / 1:1），随 payload 提交 */
  aspect: string;
  ttsEngine: "synthesize" | "user_audio";
  /** 用户录音音频绝对路径（tts_engine=user_audio 时随 payload 提交） */
  userAudioUri: string | null;
  status: RenderStatus;
  jobId: string | null;
  progress: number;
  draft: VideoDraftMeta | null;
  /** 渲染产物 content_versions(video_draft) 的版本 id（发布关联用） */
  videoVersionId: string | null;
  /** 渲染成功 detail.artifacts（video/subtitles/audio 绝对路径） */
  artifacts: RenderArtifacts | null;
  /** 费用透明：detail.invocation（执行后展示） */
  invocation: ProviderInvocation | null;
  /**
   * PRD-REN-002：旁白合成的来源与预计费用（detail.tts_invocation）。
   * 用户自带录音（user_audio）不调 TTS，故为 null。
   */
  ttsInvocation: ProviderInvocation | null;
  isBusy: boolean;
  error: string | null;

  setSourceVersion: (id: string) => void;
  setTemplate: (t: string) => void;
  setAspect: (a: string) => void;
  setTtsEngine: (e: "synthesize" | "user_audio") => void;
  setUserAudioUri: (uri: string | null) => void;
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
  aspect: "9:16",
  ttsEngine: "synthesize",
  userAudioUri: null,
  status: "idle",
  jobId: null,
  progress: 0,
  draft: null,
  videoVersionId: null,
  artifacts: null,
  invocation: null,
  ttsInvocation: null,
  isBusy: false,
  error: null,

  setSourceVersion: (id) => set({ sourceVersionId: id }),
  setTemplate: (t) => set({ template: t }),
  setAspect: (a) => set({ aspect: a }),
  setTtsEngine: (e) => set({ ttsEngine: e }),
  setUserAudioUri: (uri) => set({ userAudioUri: uri }),

  render: async () => {
    if (get().isBusy) return;
    if (get().ttsEngine === "user_audio" && !get().userAudioUri) {
      set({ error: "请先选择用户录音音频文件" });
      return;
    }
    set({ status: "running", isBusy: true, error: null, progress: 0.05 });
    try {
      const payload: CreateRenderJobPayload = {
        source_version_id: get().sourceVersionId,
        template: get().template,
        aspect: get().aspect,
        tts_engine: get().ttsEngine,
        user_audio_uri:
          get().ttsEngine === "user_audio" ? get().userAudioUri : null,
      };
      const env = buildEnvelope(
        "CreateRenderJob",
        getWorkspaceId(),
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
      // Tranche 2：渲染成功 detail 携带 artifacts（video/subtitles/audio）
      // 与 invocation（provider/model/estimated_cost）；旧后端缺失时为 null
      const rawArtifacts = detail.artifacts as
        | { video?: string; subtitles?: string | null; audio?: string | null }
        | undefined;
      set({
        status: "succeeded",
        jobId: res.job_id ?? null,
        progress: 1,
        videoVersionId: res.artifact_ids[0] ?? null,
        artifacts: rawArtifacts?.video
          ? {
              video: rawArtifacts.video,
              subtitles: rawArtifacts.subtitles ?? null,
              audio: rawArtifacts.audio ?? null,
            }
          : null,
        invocation: (detail.invocation as ProviderInvocation | undefined) ?? null,
        ttsInvocation:
          (detail.tts_invocation as ProviderInvocation | undefined) ?? null,
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
      getWorkspaceId(),
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
    if (p.job_type !== "render_source") return;
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
    set({
      error: null,
      status: "idle",
      draft: null,
      jobId: null,
      progress: 0,
      videoVersionId: null,
      artifacts: null,
      invocation: null,
      ttsInvocation: null,
    });
    await get().render();
  },

  reset: () =>
    set({
      status: "idle",
      jobId: null,
      progress: 0,
      draft: null,
      videoVersionId: null,
      artifacts: null,
      invocation: null,
      ttsInvocation: null,
      error: null,
      isBusy: false,
    }),
}));
