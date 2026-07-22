/**
 * 内容分析 Store（W4 Batch3）
 * - provider-switch：cloud / openai-compatible / ollama 三种后端
 * - 对转写 dispatch AnalyzeSource，payload.provider 携带当前选择，
 *   使前端的 provider 切换真正生效（worker 端按 hint 构造 provider）
 * - 失败保留 error，支持重试（PRD §338）
 *
 * 安全：api_key 仅由运行时输入，绝不写死、绝不落库。
 */

import { create } from "zustand";
import { buildEnvelope, dispatchCommand } from "@/lib/tauri";
import type {
  AnalysisChapter,
  AnalysisReport,
  AnalysisStatus,
  AnalysisTopic,
  ProviderConfig,
  ProviderKind,
} from "@/lib/types";

const WORKSPACE = "ws-local";

interface AnalysisStoreState {
  reports: AnalysisReport[];
  provider: ProviderConfig;
  isBusy: boolean;
  error: string | null;
  setProviderKind: (kind: ProviderKind) => void;
  setProviderField: (field: "base_url" | "api_key" | "model", value: string) => void;
  analyze: (transcriptVersionId: string, brand?: string) => Promise<void>;
  reset: () => void;
}

function blankReport(): AnalysisReport {
  return {
    status: "pending" as AnalysisStatus,
    summary: "",
    chapters: [] as AnalysisChapter[],
    topics: [] as AnalysisTopic[],
    sentiment: null,
    provider: null,
    model: null,
    confidence: null,
    created_at: null,
    error: null,
  };
}

export const useAnalysisStore = create<AnalysisStoreState>((set, get) => ({
  reports: [],
  provider: {
    kind: "ollama" as ProviderKind,
    base_url: "http://localhost:11434/v1",
    api_key: "",
    model: "llama3.1",
  },
  isBusy: false,
  error: null,

  setProviderKind: (kind) =>
    set((s) => ({ provider: { ...s.provider, kind } })),

  setProviderField: (field, value) =>
    set((s) => ({ provider: { ...s.provider, [field]: value } })),

  analyze: async (transcriptVersionId, brand) => {
    const cfg = get().provider;
    // ollama 一般不要求 key；cloud / openai-compatible 需要
    if (cfg.kind !== "ollama" && !cfg.api_key) {
      set({ error: "该 Provider 需要 API Key" });
      return;
    }
    const report = blankReport();
    set({
      reports: [...get().reports, { ...report, provider: cfg.kind }],
      isBusy: true,
      error: null,
    });

    const apply = (changes: Partial<AnalysisReport>) =>
      set((s) => ({
        reports: s.reports.map((r) =>
          r === get().reports.find((x) => x.provider === cfg.kind && x.status === "pending")
            ? { ...r, ...changes }
            : r,
        ),
      }));

    try {
      const env = buildEnvelope("AnalyzeSource", WORKSPACE, null, {
        transcript_version_id: transcriptVersionId,
        brand: brand ?? null,
        provider: {
          kind: cfg.kind,
          base_url: cfg.base_url,
          api_key: cfg.api_key,
          model: cfg.model,
        },
      });
      const res = await dispatchCommand(env);
      if (!res.ok) {
        throw new Error(res.error ?? "ANALYSIS_FAILED");
      }
      const detail = (res.detail ?? {}) as Record<string, unknown>;
      apply({
        status: "succeeded" as AnalysisStatus,
        provider: (detail.provider as string | undefined) ?? cfg.kind,
        model: (detail.model as string | undefined) ?? cfg.model,
        confidence: (detail.confidence as number | undefined) ?? null,
        created_at: new Date().toISOString(),
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      apply({ status: "failed" as AnalysisStatus, error: msg });
      set({ error: msg });
    } finally {
      set({ isBusy: false });
    }
  },

  reset: () => set({ reports: [], error: null }),
}));
