/**
 * 素材导入 Store（W3 Batch3）
 * - drag-drop / 文件选择 → 逐个 dispatch ImportSource
 * - 乐观更新：每个素材对应一条 SourceAsset 记录并跟踪状态
 * - 真实环境由 worker 落库去重；mock 环境仅做前端占位
 */

import { create } from "zustand";
import { buildEnvelope, dispatchCommand } from "@/lib/tauri";
import type { ImportStatus, MediaMeta, SourceAsset } from "@/lib/types";

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
  reset: () => void;
}

export const useImportStore = create<ImportStoreState>((set, get) => ({
  assets: [],
  isBusy: false,
  error: null,

  importFiles: async (files) => {
    if (files.length === 0) return;
    set({ isBusy: true, error: null });
    try {
      for (const f of files) {
        const kind = kindFromMime(f.mimeType);
        const env = buildEnvelope("ImportSource", WORKSPACE, null, {
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
        const assetId = res.artifact_ids[0] ?? `asset-${Date.now()}`;
        const asset: SourceAsset = {
          id: assetId,
          project_id: WORKSPACE,
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

  reset: () => set({ assets: [], error: null }),
}));
