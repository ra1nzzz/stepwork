/**
 * 素材导入 Store（W3 Batch3 → Tranche 2 扩展）
 * - drag-drop / 文件选择 → 逐个 dispatch ImportSource
 * - 乐观更新：每个素材对应一条 SourceAsset 记录并跟踪状态
 * - 真实环境由 worker 落库去重
 * - Tranche 2：
 *   * importUrl：链接直链导入（PRD-SRC-002），状态三态
 *     成功 / 失败（DOWNLOAD_FAILED）/ 需要登录（NEED_LOGIN）
 *   * 来源记录（PRD-SRC-003）：original_url / author / rights_declaration
 *     随 payload 提交（本地与链接导入共用）
 *   * dedup 命中（detail.dedup==true）→ dedupNotice 提醒（SRC-004）
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
import type {
  ImportSourcePayload,
  ImportStatus,
  MediaMeta,
  SourceAsset,
} from "@/lib/types";
import { useViewStore } from "@/stores/useViewStore";

const WORKSPACE = "ws-local";

function kindFromMime(mime: string): string {
  if (mime.startsWith("audio/")) return "audio";
  if (mime.startsWith("video/")) return "video";
  return "document";
}

export interface ImportFileInput {
  /** 文件的真实绝对路径（Tauri dialog / 拖放事件返回） */
  uri: string;
  name: string;
  /** 字节大小；dialog 选择路径时无法得知，缺省由 worker 落库时补全 */
  sizeBytes?: number;
  mimeType: string;
}

/** 来源记录（PRD-SRC-003）：随本地 / 链接导入 payload 提交 */
export interface SourceMetaInput {
  originalUrl?: string;
  author?: string;
  rightsDeclaration?: "original" | "licensed" | "reference" | "";
}

/** 链接导入三态（PRD-SRC-002：成功 / 失败 / 需要登录） */
export type UrlImportStatus =
  | "idle"
  | "importing"
  | "success"
  | "need_login"
  | "failed";

interface ImportStoreState {
  assets: SourceAsset[];
  isBusy: boolean;
  error: string | null;
  /** dedup 命中提示（SRC-004；新导入成功且无命中时清空） */
  dedupNotice: string | null;
  urlStatus: UrlImportStatus;
  urlError: string | null;
  importFiles: (files: ImportFileInput[], meta?: SourceMetaInput) => Promise<void>;
  importUrl: (url: string, meta?: SourceMetaInput) => Promise<void>;
  reset: () => Promise<void>;
}

/** 确保 selectedProjectId 有值；为空时自动创建 draft 项目并写回 viewStore */
async function ensureProjectId(): Promise<string> {
  const existing = useViewStore.getState().selectedProjectId;
  if (existing) return existing;
  const title = `草稿项目 ${new Date().toLocaleString("zh-CN")}`;
  const env = buildEnvelope("CreateProject", WORKSPACE, null, { title });
  const res = await dispatchCommand(env);
  if (!res.ok) throw new Error(res.error ?? "CREATE_PROJECT_FAILED");
  const detail = (res.detail ?? {}) as Record<string, unknown>;
  const project = (detail.project as { id?: string; title?: string } | undefined) ?? {};
  const id = project.id;
  if (!id) throw new Error("CREATE_PROJECT_NO_ID");
  useViewStore.getState().setSelectedProjectId(id, project.title ?? title);
  return id;
}

/** 把来源记录字段并入 payload（空值不提交，保持向后兼容） */
function applySourceMeta(
  payload: ImportSourcePayload,
  meta?: SourceMetaInput,
): ImportSourcePayload {
  if (!meta) return payload;
  if (meta.originalUrl?.trim()) payload.original_url = meta.originalUrl.trim();
  if (meta.author?.trim()) payload.author = meta.author.trim();
  if (meta.rightsDeclaration) payload.rights_declaration = meta.rightsDeclaration;
  return payload;
}

export const useImportStore = create<ImportStoreState>((set, get) => ({
  assets: [],
  isBusy: false,
  error: null,
  dedupNotice: null,
  urlStatus: "idle",
  urlError: null,

  importFiles: async (files, meta) => {
    if (files.length === 0) return;
    set({ isBusy: true, error: null, dedupNotice: null });
    try {
      const projectId = await ensureProjectId();
      for (const f of files) {
        const kind = kindFromMime(f.mimeType);
        const payload = applySourceMeta(
          {
            local_uri: f.uri,
            kind,
            metadata: {
              name: f.name,
              size_bytes: f.sizeBytes ?? null,
              mime_type: f.mimeType,
            },
          },
          meta,
        );
        const env = buildEnvelope("ImportSource", WORKSPACE, projectId, payload);
        const res = await dispatchCommand(env);
        if (!res.ok) {
          throw new Error(res.error ?? "IMPORT_FAILED");
        }
        const detail = (res.detail ?? {}) as Record<string, unknown>;
        const assetId = res.artifact_ids[0];
        if (!assetId) {
          // 后端必须返回 asset_id，否则下游 TranscribeSource 必然失败
          throw new Error("IMPORT_NO_ASSET_ID");
        }
        // dedup 命中：提醒用户该文件已导入过（SRC-004）
        if (detail.dedup === true) {
          set({ dedupNotice: `「${f.name}」已导入过，已复用现有素材记录。` });
        }
        // dedup 命中时后端返回既有 asset id，避免列表重复展示
        if (get().assets.some((a) => a.id === assetId)) continue;
        const asset: SourceAsset = {
          id: assetId,
          project_id: projectId,
          kind,
          local_uri: f.uri,
          original_uri: meta?.originalUrl?.trim() || null,
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

  importUrl: async (url, meta) => {
    const trimmed = url.trim();
    if (!trimmed) return;
    set({ urlStatus: "importing", urlError: null, dedupNotice: null });
    try {
      const projectId = await ensureProjectId();
      const payload = applySourceMeta({ url: trimmed }, meta);
      // 来源链接缺省时以下载链接本身作为来源记录
      if (!payload.original_url) payload.original_url = trimmed;
      const env = buildEnvelope("ImportSource", WORKSPACE, projectId, payload);
      const res = await dispatchCommand(env);
      if (!res.ok) {
        const err = res.error ?? "DOWNLOAD_FAILED";
        // DispatchError 序列化为 "CODE: message" → 按前缀映射三态
        if (err.startsWith("NEED_LOGIN")) {
          set({ urlStatus: "need_login", urlError: err });
        } else {
          set({ urlStatus: "failed", urlError: err });
        }
        return;
      }
      const detail = (res.detail ?? {}) as Record<string, unknown>;
      const assetId = res.artifact_ids[0];
      if (!assetId) throw new Error("IMPORT_NO_ASSET_ID");
      if (detail.dedup === true) {
        set({ dedupNotice: "该链接对应的文件已导入过，已复用现有素材记录。" });
      }
      if (!get().assets.some((a) => a.id === assetId)) {
        const asset: SourceAsset = {
          id: assetId,
          project_id: projectId,
          kind: (detail.kind as string | undefined) ?? "video",
          local_uri: (detail.local_uri as string | undefined) ?? trimmed,
          original_uri: trimmed,
          content_hash: "",
          import_status: "done" as ImportStatus,
          created_at: new Date().toISOString(),
          media_meta: null as MediaMeta | null,
        };
        set({ assets: [...get().assets, asset] });
      }
      set({ urlStatus: "success" });
    } catch (e) {
      set({
        urlStatus: "failed",
        urlError: e instanceof Error ? e.message : String(e),
      });
    }
  },

  reset: async () => {
    const assets = get().assets;
    // 通知后端删除 source_assets 记录（失败静默忽略，前端仍清空）
    for (const a of assets) {
      try {
        const env = buildEnvelope("DeleteAsset", WORKSPACE, a.project_id, {
          assetId: a.id,
        });
        await dispatchCommand(env);
      } catch {
        // 静默：后端删除失败不阻塞前端清空
      }
    }
    set({ assets: [], error: null, dedupNotice: null, urlStatus: "idle", urlError: null });
  },
}));
