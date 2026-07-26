/**
 * 素材导入 Store（W3 Batch3）
 * - drag-drop / 文件选择 → 逐个 dispatch ImportSource
 * - 乐观更新：每个素材对应一条 SourceAsset 记录并跟踪状态
 * - 真实环境由 worker 落库去重
 *
 * projectId 联动：
 *   - 从 useViewStore.selectedProjectId 读取
 *   - 若为空（用户直接进创作页），自动调 CreateProject 创建一个 draft 项目
 *     并写回 useViewStore，保证全流程 projectId 不为 null
 *   - buildEnvelope 的 projectId 参数透传该值
 *
 * reset：调 DeleteAsset 通知后端清理 source_assets 记录，再清空前端
 */

import { create } from "zustand";
import { buildEnvelope, dispatchCommand } from "@/lib/tauri";
import type { ImportStatus, MediaMeta, SourceAsset } from "@/lib/types";
import { useViewStore } from "@/stores/useViewStore";

const WORKSPACE = "ws-local";

function kindFromMime(mime: string): string {
  if (mime.startsWith("audio/")) return "audio";
  if (mime.startsWith("video/")) return "video";
  return "document";
}

export interface ImportFileInput {
  uri: string;
  name: string;
  sizeBytes: number;
  mimeType: string;
}

interface ImportStoreState {
  assets: SourceAsset[];
  isBusy: boolean;
  error: string | null;
  importFiles: (files: ImportFileInput[]) => Promise<void>;
  reset: () => Promise<void>;
}

/** 确保 selectedProjectId 有值；为空时自动创建 draft 项目并写回 viewStore */
async function ensureProjectId(): Promise<string> {
  const existing = useViewStore.getState().selectedProjectId;
  if (existing) return existing;
  const env = buildEnvelope("CreateProject", WORKSPACE, null, {
    title: `草稿项目 ${new Date().toLocaleString("zh-CN")}`,
  });
  const res = await dispatchCommand(env);
  if (!res.ok) throw new Error(res.error ?? "CREATE_PROJECT_FAILED");
  const detail = (res.detail ?? {}) as Record<string, unknown>;
  const project = (detail.project as { id?: string } | undefined) ?? {};
  const id = project.id;
  if (!id) throw new Error("CREATE_PROJECT_NO_ID");
  useViewStore.getState().setSelectedProjectId(id);
  return id;
}

export const useImportStore = create<ImportStoreState>((set, get) => ({
  assets: [],
  isBusy: false,
  error: null,

  importFiles: async (files) => {
    if (files.length === 0) return;
    set({ isBusy: true, error: null });
    try {
      const projectId = await ensureProjectId();
      for (const f of files) {
        const kind = kindFromMime(f.mimeType);
        const env = buildEnvelope("ImportSource", WORKSPACE, projectId, {
          local_uri: f.uri,
          kind,
          metadata: {
            name: f.name,
            size_bytes: f.sizeBytes,
            mime_type: f.mimeType,
          },
        });
        const res = await dispatchCommand(env);
        if (!res.ok) {
          throw new Error(res.error ?? "IMPORT_FAILED");
        }
        const assetId = res.artifact_ids[0];
        if (!assetId) {
          // 后端必须返回 asset_id，否则下游 TranscribeSource 必然失败
          throw new Error("IMPORT_NO_ASSET_ID");
        }
        const asset: SourceAsset = {
          id: assetId,
          project_id: projectId,
          kind,
          local_uri: f.uri,
          original_uri: null,
          content_hash: "",
          import_status: "done" as ImportStatus,
          created_at: new Date().toISOString(),
          media_meta: null as MediaMeta | null,
        };
        set({ assets: [...get().assets, asset] });
      }
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    } finally {
      set({ isBusy: false });
    }
  },

  reset: async () => {
    const assets = get().assets;
    // 通知后端删除 source_assets 记录（失败静默忽略，前端仍清空）
    for (const a of assets) {
      try {
        const env = buildEnvelope("DeleteAsset", WORKSPACE, null, {
          assetId: a.id,
        });
        await dispatchCommand(env);
      } catch {
        // 静默：后端删除失败不阻塞前端清空
      }
    }
    set({ assets: [], error: null });
  },
}));
