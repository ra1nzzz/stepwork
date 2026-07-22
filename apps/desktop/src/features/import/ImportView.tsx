/**
 * 素材导入视图（W3 Batch3）
 * 拖放 / 文件选择 → 逐个 dispatch ImportSource，按内容哈希去重
 */

import { useRef, useState, type DragEvent } from "react";
import { useImportStore, type ImportFileInput } from "@/stores/useImportStore";

function statusLabel(s: string): string {
  if (s === "done") return "已导入";
  if (s === "error") return "失败";
  if (s === "importing") return "导入中";
  return "待处理";
}

export function ImportView() {
  const assets = useImportStore((s) => s.assets);
  const isBusy = useImportStore((s) => s.isBusy);
  const error = useImportStore((s) => s.error);
  const importFiles = useImportStore((s) => s.importFiles);
  const reset = useImportStore((s) => s.reset);
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const inputs: ImportFileInput[] = Array.from(files).map((f) => ({
      uri: f.name, // mock 路径；真实 Tauri 环境由 fs 解析绝对路径
      name: f.name,
      sizeBytes: f.size,
      mimeType: f.type || "application/octet-stream",
    }));
    void importFiles(inputs);
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    handleFiles(e.dataTransfer.files);
  };

  return (
    <section className="feature-view" data-od-id="import-view">
      <header className="feature-head">
        <h1>素材导入</h1>
        <p className="feature-sub">拖入视频 / 音频 / 文档，按内容哈希去重导入</p>
      </header>

      <div
        className={`dropzone${isDragging ? " dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={onDrop}
        data-od-id="import-dropzone"
      >
        <p>把文件拖到这里，或</p>
        <button
          type="button"
          className="btn primary"
          onClick={() => inputRef.current?.click()}
          disabled={isBusy}
        >
          {isBusy ? "导入中…" : "选择文件"}
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="video/*,audio/*,.txt,.md,.srt,.vtt"
          style={{ display: "none" }}
          onChange={(e) => {
            handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {error && (
        <p className="error-text" data-od-id="import-error">
          {error}
        </p>
      )}

      <ul className="asset-list">
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

      {assets.length > 0 && (
        <button type="button" className="btn ghost" onClick={() => reset()}>
          清空列表
        </button>
      )}
    </section>
  );
}
