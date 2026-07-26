/**
 * 内容分析 Store（W4 Batch3 → Tranche 2 扩展）
 * - provider 来源：useSettingsStore.settings.llm（用户在设置页配置）
 *   analyze 时实时读取，确保用户改 Settings 立即生效
 * - 对转写 dispatch AnalyzeSource，payload.provider 携带映射后的 snake_case 配置
 * - Tranche 2：分析成功后经 GetContentVersion 拉取完整报告 JSON
 *   （summary/hook/structure/topics/key_points/risks/…），支持编辑后
 *   SaveAnalysis 生成新版本（版本链，不覆盖历史）；记录 detail.invocation
 *   实现费用透明（PRD-ANA-006）
 * - 失败保留 error，支持重试（PRD §338）
 *
 * 安全：api_key 仅由 SettingsStore 持有内存，绝不写死、绝不落库。
 */

import { create } from "zustand";
import { buildEnvelope, dispatchCommand } from "@/lib/tauri";
import { useSettingsStore } from "@/stores/useSettingsStore";
import { useViewStore } from "@/stores/useViewStore";
import type {
  AnalysisMode,
  AnalysisReport,
  AnalysisReportData,
  AnalysisStatus,
  ContentVersionDetail,
  ProviderInvocation,
  SaveAnalysisPayload,
} from "@/lib/types";

const WORKSPACE = "ws-local";

/** 当前选中项目 id（envelope.projectId）；确实没有时才回落 null */
const currentProjectId = (): string | null =>
  useViewStore.getState().selectedProjectId ?? null;

interface AnalysisStoreState {
  reports: AnalysisReport[];
  isBusy: boolean;
  isSaving: boolean;
  error: string | null;
  /**
   * 分析转写稿。mode='precise'（PRD-ANA-003）在转写文本之上叠加场景切分/
   * 关键帧，需提供媒体源 assetId；ffmpeg 不可用时后端回 UNAVAILABLE。
   */
  analyze: (
    transcriptVersionId: string,
    brand?: string,
    opts?: { mode?: AnalysisMode; assetId?: string | null },
  ) => Promise<void>;
  /** 编辑保存：以当前报告为 parent 生成新的 analysis 版本（绝不覆盖） */
  saveAnalysis: (data: AnalysisReportData) => Promise<void>;
  reset: () => void;
}

function blankReport(): AnalysisReport {
  return {
    status: "pending" as AnalysisStatus,
    mode: "quick" as AnalysisMode,
    sceneCount: 0,
    versionId: null,
    data: null,
    invocation: null,
    provider: null,
    model: null,
    confidence: null,
    created_at: null,
    error: null,
  };
}

/** 字符串数组字段的宽松归一化（旧版本报告可能缺字段） */
function asStringArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.map(String);
}

/** 解析 content_versions(analysis) 的 JSON 全文为完整报告数据 */
export function parseReportData(raw: string): AnalysisReportData | null {
  try {
    const obj = JSON.parse(raw) as Record<string, unknown>;
    return {
      summary: String(obj.summary ?? ""),
      hook: obj.hook != null ? String(obj.hook) : null,
      structure: asStringArray(obj.structure),
      topics: asStringArray(obj.topics),
      key_points: asStringArray(obj.key_points),
      risks: asStringArray(obj.risks),
      sentiment: obj.sentiment != null ? String(obj.sentiment) : null,
      suggested_title:
        obj.suggested_title != null ? String(obj.suggested_title) : null,
      suggested_tags: asStringArray(obj.suggested_tags),
      target_audience:
        obj.target_audience != null ? String(obj.target_audience) : null,
      provider: String(obj.provider ?? ""),
      model: String(obj.model ?? ""),
      confidence:
        typeof obj.confidence === "number" ? obj.confidence : null,
    };
  } catch {
    return null;
  }
}

/** 经 GetContentVersion 拉取版本全文（失败返回 null，不阻塞主流程） */
async function fetchVersionContent(versionId: string): Promise<string | null> {
  try {
    const env = buildEnvelope("GetContentVersion", WORKSPACE, currentProjectId(), {
      versionId,
    });
    const res = await dispatchCommand(env);
    if (!res.ok) return null;
    const detail = (res.detail ?? {}) as { version?: ContentVersionDetail };
    return detail.version?.content ?? null;
  } catch {
    return null;
  }
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
  isSaving: false,
  error: null,

  analyze: async (transcriptVersionId, brand, opts) => {
    const mode: AnalysisMode = opts?.mode ?? "quick";
    const cfg = readProviderFromSettings();
    // ollama 一般不要求 key；cloud / openai-compatible 需要
    if (cfg.kind !== "ollama" && !cfg.api_key) {
      set({ error: "请先在设置页配置 LLM API Key" });
      return;
    }
    const report = blankReport();
    set({
      reports: [...get().reports, { ...report, mode, provider: cfg.kind }],
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
      const payload: Record<string, unknown> = {
        transcript_version_id: transcriptVersionId,
        brand: brand ?? null,
        provider: {
          kind: cfg.kind,
          base_url: cfg.base_url,
          api_key: cfg.api_key,
          model: cfg.model,
        },
      };
      // 精确模式才下发 mode + 媒体源，quick 信封保持与旧版一致
      if (mode === "precise") {
        payload.mode = "precise";
        if (opts?.assetId) payload.asset_id = opts.assetId;
      }
      const env = buildEnvelope(
        "AnalyzeSource",
        WORKSPACE,
        currentProjectId(),
        payload,
      );
      const res = await dispatchCommand(env);
      if (!res.ok) {
        throw new Error(res.error ?? "ANALYSIS_FAILED");
      }
      const detail = (res.detail ?? {}) as Record<string, unknown>;
      const versionId = res.artifact_ids[0] ?? null;
      // 拉取完整报告全文（summary/hook/structure/… 全字段）
      let data: AnalysisReportData | null = null;
      if (versionId) {
        const content = await fetchVersionContent(versionId);
        if (content) data = parseReportData(content);
      }
      apply({
        status: "succeeded" as AnalysisStatus,
        sceneCount: (detail.scene_count as number | undefined) ?? 0,
        versionId,
        data,
        invocation:
          (detail.invocation as ProviderInvocation | undefined) ?? null,
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

  saveAnalysis: async (data) => {
    const reports = get().reports;
    const latestIndex = reports.length - 1;
    const latest = reports[latestIndex];
    if (!latest || latest.status !== "succeeded") {
      set({ error: "尚无可保存的分析报告" });
      return;
    }
    set({ isSaving: true, error: null });
    try {
      const projectId = currentProjectId();
      const payload: SaveAnalysisPayload = {
        content: JSON.stringify(data),
        parentVersionId: latest.versionId,
      };
      if (projectId) payload.projectId = projectId;
      const env = buildEnvelope("SaveAnalysis", WORKSPACE, projectId, payload);
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "SAVE_ANALYSIS_FAILED");
      const detail = (res.detail ?? {}) as { version_id?: string };
      const newVersionId = detail.version_id ?? res.artifact_ids[0] ?? null;
      // 新版本串链：最新报告条目指向新版本（历史版本在后端版本链中保留）
      set((s) => ({
        reports: s.reports.map((r, i) =>
          i === latestIndex
            ? { ...r, versionId: newVersionId ?? r.versionId, data }
            : r,
        ),
      }));
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    } finally {
      set({ isSaving: false });
    }
  },

  reset: () => set({ reports: [], error: null }),
}));
