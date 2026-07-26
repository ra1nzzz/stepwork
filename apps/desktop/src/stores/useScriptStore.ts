/**
 * 脚本创作 Store（W5 Batch1 → Tranche 2 扩展）
 * - 对转写/素材 dispatch GenerateTopic → 拿到差异化角度
 * - 选定角度 dispatch GenerateScript → 拿到脚本正文（seed 编辑器）
 * - 编辑器内容变化（TipTap JSON）经防抖后 dispatch SaveScript 自动保存，
 *   每次保存新建一条 script 版本并串 parent 链（防丢稿）
 * - Tranche 2（WS-005 恢复闭环）：
 *   loadVersions 经 ListContentVersions 恢复项目最近的 script 版本链，
 *   并把最新版本内容 seed 回编辑器；loadVersionContent 经 GetContentVersion
 *   加载任意历史版本全文；回滚 = 以历史内容 SaveScript 生成新版本，不覆盖历史
 *
 * 与 W3/W4 store 一致：经由 buildEnvelope + dispatchCommand
 */

import { create } from "zustand";
import { buildEnvelope, dispatchCommand } from "@/lib/tauri";
import { useViewStore } from "@/stores/useViewStore";
import type {
  TopicAngle,
  ScriptVersionRef,
  GenerateTopicPayload,
  GenerateScriptPayload,
  SaveScriptPayload,
  ContentVersionSummary,
  ContentVersionDetail,
} from "@/lib/types";

const WORKSPACE = "ws-local";

/** 当前选中项目 id（envelope.projectId）；确实没有时才回落 null */
const currentProjectId = (): string | null =>
  useViewStore.getState().selectedProjectId ?? null;

export type ScriptStatus = "idle" | "running" | "succeeded" | "failed";

interface ScriptStoreState {
  /** 选题来源的转写/素材版本 id */
  sourceVersionId: string;
  angles: TopicAngle[];
  proposalVersionId: string | null;
  selectedAngleId: string | null;
  topicStatus: ScriptStatus;
  /** 生成脚本的标题（编辑器头部可改） */
  scriptTitle: string;
  /** 待 seed 到编辑器的纯文本（来自生成脚本 body） */
  seedBody: string | null;
  /** 当前已保存脚本版本 id（作为下次保存的 parent） */
  scriptVersionId: string | null;
  versionChain: ScriptVersionRef[];
  isBusy: boolean;
  error: string | null;

  setSourceVersion: (id: string) => void;
  selectAngle: (id: string) => void;
  /** PRD-BRD-002：生成时是否启用项目绑定的品牌档（默认启用） */
  useBrandProfile: boolean;
  setUseBrandProfile: (v: boolean) => void;
  generateTopics: () => Promise<void>;
  generateScript: () => Promise<void>;
  setScriptTitle: (t: string) => void;
  /** 保存正文（可选标题）到后端，新建版本并串链 */
  saveScript: (text: string, title?: string) => Promise<void>;
  /** 进入创作页时恢复项目的 script 版本链，并 seed 最新版本内容（WS-005） */
  loadVersions: () => Promise<void>;
  /** 加载指定版本全文（版本 tab 点击查看/回滚用） */
  loadVersionContent: (
    versionId: string,
  ) => Promise<{ title: string; body: string } | null>;
  reset: () => void;
}

/** 解析 script 版本内容：生成路径为 {title, body}，保存路径为 {text, title}，
 *  历史/外部内容可能是裸文本 → 整体作为 body */
function parseScriptContent(raw: string): { title: string; body: string } {
  try {
    const obj = JSON.parse(raw) as Record<string, unknown>;
    const body =
      typeof obj.body === "string"
        ? obj.body
        : typeof obj.text === "string"
          ? obj.text
          : raw;
    return { title: typeof obj.title === "string" ? obj.title : "", body };
  } catch {
    return { title: "", body: raw };
  }
}

function newVersionRef(
  id: string,
  parent: string | null,
  producer: string,
): ScriptVersionRef {
  return {
    id,
    parent_version_id: parent,
    created_at: new Date().toISOString(),
    producer_kind: producer,
  };
}

export const useScriptStore = create<ScriptStoreState>((set, get) => ({
  sourceVersionId: "",
  angles: [],
  proposalVersionId: null,
  selectedAngleId: null,
  topicStatus: "idle",
  scriptTitle: "",
  seedBody: null,
  scriptVersionId: null,
  versionChain: [],
  isBusy: false,
  error: null,

  setSourceVersion: (id) => set({ sourceVersionId: id }),

  selectAngle: (id) => set({ selectedAngleId: id }),

  useBrandProfile: true,
  setUseBrandProfile: (v) => set({ useBrandProfile: v }),

  generateTopics: async () => {
    if (get().isBusy) return;
    set({ topicStatus: "running", isBusy: true, error: null });
    try {
      const payload: GenerateTopicPayload = {
        source_version_id: get().sourceVersionId,
        count: 3,
        use_brand_profile: get().useBrandProfile,
      };
      const env = buildEnvelope(
        "GenerateTopic",
        WORKSPACE,
        currentProjectId(),
        payload,
      );
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "TOPIC_FAILED");
      const detail = (res.detail ?? {}) as Record<string, unknown>;
      const angles = (detail.angles as TopicAngle[] | undefined) ?? [];
      set({
        angles,
        proposalVersionId: res.artifact_ids[0] ?? null,
        selectedAngleId: angles[0]?.id ?? null,
        topicStatus: "succeeded",
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      set({ topicStatus: "failed", error: msg });
    } finally {
      set({ isBusy: false });
    }
  },

  generateScript: async () => {
    if (get().isBusy) return;
    const proposalId = get().proposalVersionId;
    if (!proposalId) {
      set({ error: "请先生成选题角度" });
      return;
    }
    set({ isBusy: true, error: null });
    try {
      const payload: GenerateScriptPayload = {
        proposal_version_id: proposalId,
        topic_id: get().selectedAngleId,
        style: "short_video",
        use_brand_profile: get().useBrandProfile,
      };
      const env = buildEnvelope(
        "GenerateScript",
        WORKSPACE,
        currentProjectId(),
        payload,
      );
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "SCRIPT_FAILED");
      const detail = (res.detail ?? {}) as Record<string, unknown>;
      const script = (detail.script as { title?: string; body?: string } | undefined) ?? {};
      const versionId = res.artifact_ids[0] ?? null;
      set((s) => ({
        scriptTitle: script.title ?? s.scriptTitle,
        seedBody: script.body ?? null,
        scriptVersionId: versionId,
        versionChain: versionId
          ? [newVersionRef(versionId, s.scriptVersionId, "ai-script"), ...s.versionChain]
          : s.versionChain,
      }));
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      set({ error: msg });
    } finally {
      set({ isBusy: false });
    }
  },

  setScriptTitle: (t) => set({ scriptTitle: t }),

  saveScript: async (text, title) => {
    const payload: SaveScriptPayload = {
      content: { text, title: title ?? get().scriptTitle },
      parent_version_id: get().scriptVersionId,
    };
    const env = buildEnvelope(
      "SaveScript",
      WORKSPACE,
      currentProjectId(),
      payload,
    );
    try {
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "SAVE_FAILED");
      const versionId = res.artifact_ids[0] ?? null;
      if (versionId) {
        set((s) => ({
          scriptVersionId: versionId,
          versionChain: [
            newVersionRef(versionId, s.scriptVersionId, "user-script"),
            ...s.versionChain,
          ],
        }));
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      set({ error: msg });
    }
  },

  loadVersions: async () => {
    const projectId = currentProjectId();
    if (!projectId) return;
    try {
      const env = buildEnvelope("ListContentVersions", WORKSPACE, projectId, {
        projectId,
        contentType: "script",
        limit: 20,
      });
      const res = await dispatchCommand(env);
      if (!res.ok) return;
      const detail = (res.detail ?? {}) as { versions?: ContentVersionSummary[] };
      const versions = detail.versions ?? [];
      if (versions.length === 0) return;
      const chain: ScriptVersionRef[] = versions.map((v) => ({
        id: v.id,
        parent_version_id: v.parent_version_id,
        created_at: v.created_at,
        producer_kind:
          typeof v.producer?.kind === "string" ? (v.producer.kind as string) : null,
      }));
      set({ versionChain: chain });
      // 恢复最新版本内容到编辑器（仅当本地尚无已保存版本，避免覆盖会话内进度）
      if (!get().scriptVersionId) {
        const latest = await get().loadVersionContent(chain[0].id);
        if (latest) {
          set((s) => ({
            scriptVersionId: chain[0].id,
            scriptTitle: latest.title || s.scriptTitle,
            seedBody: latest.body,
          }));
        }
      }
    } catch {
      /* 后端未连接：保持空链 */
    }
  },

  loadVersionContent: async (versionId) => {
    try {
      const env = buildEnvelope("GetContentVersion", WORKSPACE, currentProjectId(), {
        versionId,
      });
      const res = await dispatchCommand(env);
      if (!res.ok) return null;
      const detail = (res.detail ?? {}) as { version?: ContentVersionDetail };
      const content = detail.version?.content;
      if (content == null) return null;
      return parseScriptContent(content);
    } catch {
      return null;
    }
  },

  reset: () =>
    set({
      angles: [],
      proposalVersionId: null,
      selectedAngleId: null,
      topicStatus: "idle",
      scriptTitle: "",
      seedBody: null,
      scriptVersionId: null,
      versionChain: [],
      error: null,
    }),
}));
