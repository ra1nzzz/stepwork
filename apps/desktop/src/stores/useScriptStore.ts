/**
 * 脚本创作 Store（W5 Batch1）
 * - 对转写/素材 dispatch GenerateTopic → 拿到差异化角度
 * - 选定角度 dispatch GenerateScript → 拿到脚本正文（seed 编辑器）
 * - 编辑器内容变化（TipTap JSON）经防抖后 dispatch SaveScript 自动保存，
 *   每次保存新建一条 script 版本并串 parent 链（防丢稿）
 *
 * 与 W3/W4 store 一致：经由 buildEnvelope + dispatchCommand；
 * 浏览器下由 tauri.ts mock 返回示例数据。
 */

import { create } from "zustand";
import { buildEnvelope, dispatchCommand } from "@/lib/tauri";
import type {
  TopicAngle,
  ScriptVersionRef,
  GenerateTopicPayload,
  GenerateScriptPayload,
  SaveScriptPayload,
} from "@/lib/types";

const WORKSPACE = "ws-local";

export type ScriptStatus = "idle" | "running" | "succeeded" | "failed";

interface ScriptStoreState {
  /** 选题来源的转写/素材版本 id（默认 mock 版，便于演示） */
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
  generateTopics: () => Promise<void>;
  generateScript: () => Promise<void>;
  setScriptTitle: (t: string) => void;
  /** 编辑器内容（TipTap JSON）防抖后调用，新建版本并串链 */
  saveScript: (doc: Record<string, unknown>) => Promise<void>;
  reset: () => void;
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
  sourceVersionId: "cv-local",
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

  generateTopics: async () => {
    if (get().isBusy) return;
    set({ topicStatus: "running", isBusy: true, error: null });
    try {
      const payload: GenerateTopicPayload = {
        source_version_id: get().sourceVersionId,
        count: 3,
      };
      const env = buildEnvelope(
        "GenerateTopic",
        WORKSPACE,
        null,
        payload as unknown as Record<string, unknown>,
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
      };
      const env = buildEnvelope(
        "GenerateScript",
        WORKSPACE,
        null,
        payload as unknown as Record<string, unknown>,
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

  saveScript: async (doc) => {
    const payload: SaveScriptPayload = {
      content: doc,
      parent_version_id: get().scriptVersionId,
    };
    const env = buildEnvelope(
      "SaveScript",
      WORKSPACE,
      null,
      payload as unknown as Record<string, unknown>,
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
