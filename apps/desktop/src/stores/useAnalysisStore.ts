/**
 * 内容分析 Store（W4 Batch3）
 * - provider 来源：useSettingsStore.settings.llm（用户在设置页配置）
 *   analyze 时实时读取，确保用户改 Settings 立即生效
 * - 对转写 dispatch AnalyzeSource，payload.provider 携带映射后的 snake_case 配置
 * - 失败保留 error，支持重试（PRD §338）
 *
 * 安全：api_key 仅由 SettingsStore 持有内存，绝不写死、绝不落库。
 */

import { create } from "zustand";
import { buildEnvelope, dispatchCommand } from "@/lib/tauri";
import { useSettingsStore } from "@/stores/useSettingsStore";
import type {
  AnalysisChapter,
  AnalysisReport,
  AnalysisStatus,
  AnalysisTopic,
} from "@/lib/types";

const WORKSPACE = "ws-local";

interface AnalysisStoreState {
  reports: AnalysisReport[];
  isBusy: boolean;
  error: string | null;
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

/** 从 useSettingsStore 读取 llm 配置，映射成后端期望的 snake_case provider */
function readProviderFromSettings(): {
  kind: string;
  base_url: string;
  api_key: string;
  model: string;
} {
  const llm = useSettingsStore.getState().settings.llm;
  return {
    kind: llm.provider,
    base_url: llm.baseUrl,
    api_key: llm.apiKey,
    model: llm.model,
  };
}

export const useAnalysisStore = create<AnalysisStoreState>((set, get) => ({
  reports: [],
  isBusy: false,
  error: null,

  analyze: async (transcriptVersionId, brand) => {
    const cfg = readProviderFromSettings();
    // ollama 一般不要求 key；cloud / openai-compatible 需要
    if (cfg.kind !== "ollama" && !cfg.api_key) {
      set({ error: "请先在设置页配置 LLM API Key" });
      return;
    }
    const report = blankReport();
    set({
      reports: [...get().reports, { ...report, provider: cfg.kind }],
      isBusy: true,
      error: null,
    });

    // 用 index 定位要 patch 的 report（避免 find 命中错位）
    const reportIndex = get().reports.length - 1;
    const apply = (changes: Partial<AnalysisReport>) =>
      set((s) => ({
        reports: s.reports.map((r, i) =>
          i === reportIndex ? { ...r, ...changes } : r,
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
