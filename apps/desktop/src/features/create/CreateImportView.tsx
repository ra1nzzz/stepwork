/**
 * 创作页子视图 · 01 素材分析（顶部：导入）
 * 真实接入 useImportStore → ImportSource 命令。
 *
 * Tranche 1 / T3：文件来源改为真实绝对路径 —
 * - 点击选择：@tauri-apps/plugin-dialog open({multiple, filters}) 返回绝对路径
 * - 拖放：getCurrentWebview().onDragDropEvent 的 drop 事件携带真实 paths
 * - 非 Tauri 环境（纯浏览器）没有文件系统路径可用，仅显示提示
 *
 * Tranche 2：
 * - URL 直链导入（PRD-SRC-002）：状态三态 成功/失败/需要登录
 * - 来源记录（PRD-SRC-003）：来源链接/作者/权利声明 随 payload 提交
 * - dedup 命中提醒（SRC-004）
 *
 * 与原型 inspector.html 第一个 .layout-main 视觉对齐，但所有数据来自真实 store。
 */

import { useEffect, useRef, useState } from "react";
import {
  useImportStore,
  type ImportFileInput,
  type SourceMetaInput,
  type UrlImportStatus,
} from "@/stores/useImportStore";
import { useTranscriptStore } from "@/stores/useTranscriptStore";
import { isTauri } from "@/lib/tauri";
import { useViewStore } from "@/stores/useViewStore";

function statusLabel(s: string): string {
  if (s === "done") return "已导入";
  if (s === "error") return "失败";
  if (s === "importing") return "导入中";
  return "待处理";
}

/** dialog filters 用的扩展名分组（与 mime 推导保持一致） */
const VIDEO_EXTS = ["mp4", "mov", "mkv", "avi", "webm", "m4v"];
const AUDIO_EXTS = ["mp3", "wav", "m4a", "flac", "aac", "ogg", "opus"];
const IMAGE_EXTS = ["jpg", "jpeg", "png", "gif", "webp", "bmp"];
const TEXT_EXTS = ["txt", "md", "srt", "vtt"];

/** 扩展名 → MIME（后端按 mime 前缀归类 audio/video/document） */
const EXT_MIME: Record<string, string> = {
  mp4: "video/mp4",
  mov: "video/quicktime",
  mkv: "video/x-matroska",
  avi: "video/x-msvideo",
  webm: "video/webm",
  m4v: "video/x-m4v",
  mp3: "audio/mpeg",
  wav: "audio/wav",
  m4a: "audio/mp4",
  flac: "audio/flac",
  aac: "audio/aac",
  ogg: "audio/ogg",
  opus: "audio/opus",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  gif: "image/gif",
  webp: "image/webp",
  bmp: "image/bmp",
  txt: "text/plain",
  md: "text/markdown",
  srt: "application/x-subrip",
  vtt: "text/vtt",
};

/** 从绝对路径取文件名（Windows / POSIX 分隔符都兼容） */
function basename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

/** 绝对路径 → ImportFileInput：name 取 basename，mime 按扩展名推导 */
function pathToInput(path: string): ImportFileInput {
  const name = basename(path);
  const dot = name.lastIndexOf(".");
  const ext = dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
  return {
    uri: path,
    name,
    mimeType: EXT_MIME[ext] ?? "application/octet-stream",
  };
}

/** URL 导入三态展示（成功 / 失败 / 需要登录） */
function urlStatusView(s: UrlImportStatus): { cls: string; text: string } | null {
  switch (s) {
    case "importing": return { cls: "ai", text: "下载中" };
    case "success": return { cls: "success", text: "导入成功" };
    case "need_login": return { cls: "warning", text: "需要登录" };
    case "failed": return { cls: "danger", text: "导入失败" };
    default: return null;
  }
}

export function CreateImportView() {
  const setCreateSubView = useViewStore((s) => s.setCreateSubView);
  const assets = useImportStore((s) => s.assets);
  const isBusy = useImportStore((s) => s.isBusy);
  const error = useImportStore((s) => s.error);
  const dedupNotice = useImportStore((s) => s.dedupNotice);
  const urlStatus = useImportStore((s) => s.urlStatus);
  const urlError = useImportStore((s) => s.urlError);
  const importFiles = useImportStore((s) => s.importFiles);
  const importUrl = useImportStore((s) => s.importUrl);
  const reset = useImportStore((s) => s.reset);
  const transcriptJobs = useTranscriptStore((s) => s.jobs);
  const [isDragging, setIsDragging] = useState(false);

  // Tranche 2：链接导入 + 来源记录（PRD-SRC-002/003）
  const [importLink, setImportLink] = useState("");
  const [originalUrl, setOriginalUrl] = useState("");
  const [author, setAuthor] = useState("");
  const [rightsDeclaration, setRightsDeclaration] = useState<
    "" | "original" | "licensed" | "reference"
  >("");
  const inTauri = isTauri();

  // 拖放回调只在挂载时注册，经 ref 读取最新来源记录，避免闭包快照过期
  const metaRef = useRef<SourceMetaInput>({});
  metaRef.current = { originalUrl, author, rightsDeclaration };

  const handlePaths = (paths: string[]) => {
    if (paths.length === 0) return;
    void importFiles(paths.map(pathToInput), metaRef.current);
  };

  const handleImportUrl = () => {
    if (!importLink.trim() || urlStatus === "importing") return;
    void importUrl(importLink, metaRef.current);
  };

  /** 点击选择：Tauri dialog 返回真实绝对路径 */
  const pickFiles = async () => {
    if (!inTauri || isBusy) return;
    const { open } = await import("@tauri-apps/plugin-dialog");
    const selected = await open({
      multiple: true,
      filters: [
        { name: "视频", extensions: VIDEO_EXTS },
        { name: "音频", extensions: AUDIO_EXTS },
        { name: "图片", extensions: IMAGE_EXTS },
        { name: "文本", extensions: TEXT_EXTS },
      ],
    });
    if (!selected) return;
    handlePaths(Array.isArray(selected) ? selected : [selected]);
  };

  /** 拖放：Tauri webview 原生 drag-drop 事件（drop 携带真实 paths） */
  useEffect(() => {
    if (!inTauri) return;
    let unlisten: (() => void) | null = null;
    let disposed = false;
    void (async () => {
      const { getCurrentWebview } = await import("@tauri-apps/api/webview");
      const stop = await getCurrentWebview().onDragDropEvent((event) => {
        const payload = event.payload;
        if (payload.type === "enter" || payload.type === "over") {
          setIsDragging(true);
        } else if (payload.type === "leave") {
          setIsDragging(false);
        } else if (payload.type === "drop") {
          setIsDragging(false);
          handlePaths(payload.paths);
        }
      });
      if (disposed) stop();
      else unlisten = stop;
    })();
    return () => {
      disposed = true;
      if (unlisten) unlisten();
    };
    // importFiles 为 store 稳定引用，无需依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inTauri]);

  const importedCount = assets.length;
  const transcriptCount = transcriptJobs.length;

  return (
    <>
      <section className="page-head" data-od-id="analysis-heading">
        <div>
          <p className="eyebrow">SOURCE TO EVIDENCE</p>
          <h1>让每个观点都能回到原始素材</h1>
          <p className="page-subtitle">
            支持视频、音频、文本与逐字稿；分析结果保留时间戳、模型、费用和来源权利声明。
            当前已导入 {importedCount} 个素材，{transcriptCount} 个转写任务。
          </p>
        </div>
        <span className={`status ${error ? "danger" : isBusy ? "ai" : "success"}`}>
          {error ? "导入失败" : isBusy ? "导入中" : "就绪"}
        </span>
      </section>

      <section className="layout-main" data-od-id="import-layout">
        <div className="grid">
          <article className="panel" data-od-id="source-import-panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">添加来源素材</h2>
                <div className="panel-meta">单个文件最大 2GB，可组合多个来源</div>
              </div>
              <span className="mono panel-meta">{importedCount} SOURCES</span>
            </div>
            <div className="panel-body">
              <div
                className={`dropzone section-gap${isDragging ? " dragging" : ""}`}
                onClick={() => void pickFiles()}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") void pickFiles();
                }}
                data-od-id="source-dropzone"
                style={{ cursor: inTauri ? "pointer" : "not-allowed" }}
              >
                <span>
                  <span className="upload-glyph">IN</span>
                  <span className="drop-title">
                    {!inTauri
                      ? "浏览器环境不支持导入"
                      : isBusy
                        ? "导入中…"
                        : "拖入素材，或点击选择文件"}
                  </span>
                  <span className="drop-copy">
                    {inTauri
                      ? "上传数据仅用于当前项目，可在项目设置中删除"
                      : "文件导入需要真实文件路径，请在 STEPWORK 桌面应用中使用"}
                  </span>
                </span>
              </div>

              {/* Tranche 2：URL 直链导入（成功/失败/需要登录 三态） */}
              <div className="form-group section-gap" data-od-id="url-import-group">
                <label htmlFor="importLink">链接导入（https 直链）</label>
                <div className="inline-actions" style={{ alignItems: "stretch" }}>
                  <input
                    id="importLink"
                    className="field"
                    type="url"
                    style={{ flex: 1 }}
                    placeholder="https://example.com/video.mp4"
                    value={importLink}
                    onChange={(e) => setImportLink(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleImportUrl();
                    }}
                    disabled={urlStatus === "importing"}
                  />
                  <button
                    type="button"
                    className="btn"
                    onClick={handleImportUrl}
                    disabled={!importLink.trim() || urlStatus === "importing"}
                  >
                    {urlStatus === "importing" ? "下载中…" : "导入链接"}
                  </button>
                </div>
                {(() => {
                  const v = urlStatusView(urlStatus);
                  if (!v) return null;
                  return (
                    <p className="panel-meta" style={{ marginTop: 8 }}>
                      <span className={`status ${v.cls}`}>{v.text}</span>
                      {urlStatus === "need_login" && (
                        <span style={{ marginLeft: 8 }}>
                          该链接需要登录才能访问，请先在浏览器下载到本地后再导入。
                        </span>
                      )}
                      {urlStatus === "failed" && urlError && (
                        <span style={{ marginLeft: 8, color: "var(--danger)" }}>
                          {urlError}
                        </span>
                      )}
                    </p>
                  );
                })()}
              </div>

              {/* Tranche 2：来源记录（随本地/链接导入 payload 提交） */}
              <div className="grid section-gap" data-od-id="source-record-fields">
                <div className="form-group">
                  <label htmlFor="sourceOriginalUrl">来源链接</label>
                  <input
                    id="sourceOriginalUrl"
                    className="field"
                    type="url"
                    style={{ width: "100%" }}
                    placeholder="素材的原始出处链接（可选）"
                    value={originalUrl}
                    onChange={(e) => setOriginalUrl(e.target.value)}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="sourceAuthor">作者</label>
                  <input
                    id="sourceAuthor"
                    className="field"
                    style={{ width: "100%" }}
                    placeholder="原作者/创作者（可选）"
                    value={author}
                    onChange={(e) => setAuthor(e.target.value)}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="sourceRights">权利声明</label>
                  <select
                    id="sourceRights"
                    className="select"
                    value={rightsDeclaration}
                    onChange={(e) =>
                      setRightsDeclaration(
                        e.target.value as "" | "original" | "licensed" | "reference",
                      )
                    }
                  >
                    <option value="">未声明</option>
                    <option value="original">原创（original）</option>
                    <option value="licensed">已授权（licensed）</option>
                    <option value="reference">引用参考（reference）</option>
                  </select>
                </div>
              </div>

              {dedupNotice && (
                <p
                  className="panel-meta section-gap"
                  style={{ color: "var(--warning, #d97706)" }}
                  role="status"
                >
                  {dedupNotice}
                </p>
              )}

              {error && (
                <p className="error-text section-gap" style={{ color: "var(--danger)" }}>
                  {error}
                </p>
              )}

              {assets.length > 0 && (
                <ul className="asset-list section-gap">
                  {assets.map((a) => (
                    <li key={a.id} className="asset-item" data-od-id={`asset-${a.id}`}>
                      <span className="asset-name">{basename(a.local_uri)}</span>
                      <span className="asset-kind">{a.kind}</span>
                      <span className="status-badge" data-status={a.import_status}>
                        {statusLabel(a.import_status)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              {assets.length > 0 && (
                <div className="inline-actions section-gap">
                  <button
                    type="button"
                    className="btn ghost"
                    onClick={() => reset()}
                    disabled={isBusy}
                  >
                    清空列表
                  </button>
                  <button
                    type="button"
                    className="btn primary"
                    onClick={() => setCreateSubView("analysis")}
                    disabled={isBusy || assets.length === 0}
                  >
                    去转写分析
                  </button>
                </div>
              )}
            </div>
          </article>
        </div>

        <aside className="grid">
          <article className="panel" data-od-id="source-provenance-panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">导入说明</h2>
                <div className="panel-meta">真实接入 ImportSource 命令</div>
              </div>
            </div>
            <div className="panel-body">
              <dl className="provenance">
                <div className="provenance-row">
                  <dt>工作区</dt>
                  <dd className="mono">ws-local</dd>
                </div>
                <div className="provenance-row">
                  <dt>去重策略</dt>
                  <dd>content_hash 去重</dd>
                </div>
                <div className="provenance-row">
                  <dt>支持类型</dt>
                  <dd>video / audio / document</dd>
                </div>
                <div className="provenance-row">
                  <dt>存储位置</dt>
                  <dd>source_assets 表</dd>
                </div>
              </dl>
            </div>
          </article>
        </aside>
      </section>
    </>
  );
}
