/**
 * 创作页子视图 · 01 素材分析（顶部：导入）
 * 真实接入 useImportStore → ImportSource 命令。
 *
 * 与原型 inspector.html 第一个 .layout-main 视觉对齐，但所有数据来自真实 store。
 */

import { useRef, useState, type DragEvent } from "react";
import { useImportStore, type ImportFileInput } from "@/stores/useImportStore";
import { useTranscriptStore } from "@/stores/useTranscriptStore";
import { isTauri } from "@/lib/tauri";
import { useViewStore } from "@/stores/useViewStore";

function statusLabel(s: string): string {
  if (s === "done") return "已导入";
  if (s === "error") return "失败";
  if (s === "importing") return "导入中";
  return "待处理";
}

/** Tauri 2 webview 中 File 对象带有非标准 path 属性（完整文件路径） */
function fileToInput(f: File, inTauri: boolean): ImportFileInput {
  const path = inTauri ? ((f as File & { path?: string }).path ?? f.name) : URL.createObjectURL(f);
  return {
    uri: path,
    name: f.name,
    sizeBytes: f.size,
    mimeType: f.type || "application/octet-stream",
  };
}

export function CreateImportView() {
  const setCreateSubView = useViewStore((s) => s.setCreateSubView);
  const assets = useImportStore((s) => s.assets);
  const isBusy = useImportStore((s) => s.isBusy);
  const error = useImportStore((s) => s.error);
  const importFiles = useImportStore((s) => s.importFiles);
  const reset = useImportStore((s) => s.reset);
  const transcriptJobs = useTranscriptStore((s) => s.jobs);
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const inTauri = isTauri();

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const inputs = Array.from(files).map((f) => fileToInput(f, inTauri));
    void importFiles(inputs);
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    handleFiles(e.dataTransfer.files);
  };

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
                onDragOver={(e) => {
                  e.preventDefault();
                  setIsDragging(true);
                }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={onDrop}
                onClick={() => inputRef.current?.click()}
                role="button"
                tabIndex={0}
                data-od-id="source-dropzone"
                style={{ cursor: "pointer" }}
              >
                <span>
                  <span className="upload-glyph">IN</span>
                  <span className="drop-title">
                    {isBusy ? "导入中…" : "拖入素材，或点击选择文件"}
                  </span>
                  <span className="drop-copy">
                    上传数据仅用于当前项目，可在项目设置中删除
                  </span>
                </span>
              </div>
              <input
                ref={inputRef}
                className="hidden-input"
                type="file"
                multiple
                accept="video/*,audio/*,.txt,.md,.srt,.vtt"
                style={{ display: "none" }}
                onChange={(e) => {
                  handleFiles(e.target.files);
                  e.target.value = "";
                }}
              />

              {error && (
                <p className="error-text section-gap" style={{ color: "var(--danger)" }}>
                  {error}
                </p>
              )}

              {assets.length > 0 && (
                <ul className="asset-list section-gap">
                  {assets.map((a) => (
                    <li key={a.id} className="asset-item" data-od-id={`asset-${a.id}`}>
                      <span className="asset-name">{a.local_uri}</span>
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
