/**
 * 诊断视图（W8）
 * 导出脱敏诊断包（ExportDiagnosticsBundle），展示 bundle_path / size_bytes / desensitized。
 * 诊断包默认脱敏，不含明文密钥。
 */

import { useState } from "react";
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "@/lib/tauri";
import type { CommandResult } from "@/lib/types";

interface BundleResult {
  bundle_path: string;
  size_bytes: number;
  desensitized: boolean;
}

function formatSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${units[i]}`;
}

export function DiagnosticsView() {
  const [bundle, setBundle] = useState<BundleResult | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function exportBundle() {
    setIsBusy(true);
    setError(null);
    try {
      const env = buildEnvelope(
        "ExportDiagnosticsBundle",
        getWorkspaceId(),
        null,
        {},
      );
      const res: CommandResult = await dispatchCommand(env);
      if (!res.ok) {
        setError(res.error ?? "导出失败");
        setBundle(null);
      } else {
        const d = (res.detail ?? {}) as Partial<BundleResult>;
        setBundle({
          bundle_path: d.bundle_path ?? "",
          size_bytes: d.size_bytes ?? 0,
          desensitized: d.desensitized ?? true,
        });
      }
    } catch (e) {
      setError(String(e));
      setBundle(null);
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <section className="feature-view" data-od-id="diagnostics-view">
      <header className="feature-head">
        <h1>诊断</h1>
        <p className="feature-sub">导出脱敏诊断包，用于问题排查与环境快照</p>
      </header>

      <p className="feature-sub">
        诊断包默认脱敏，不含明文密钥。包含 health 摘要、DB schema 版本、配置掩码快照、最近日志。
      </p>

      <div className="diagnostics-actions">
        <button
          type="button"
          className="btn primary"
          disabled={isBusy}
          onClick={() => void exportBundle()}
        >
          {isBusy ? "导出中…" : "导出诊断包"}
        </button>
      </div>

      {error && (
        <p className="error-text" data-od-id="diagnostics-error">
          {error}
        </p>
      )}

      {bundle && (
        <div className="bundle-card" data-od-id="bundle-card">
          <h3>导出结果</h3>
          <p className="bundle-path">{bundle.bundle_path}</p>
          <p className="bundle-meta">
            大小：{formatSize(bundle.size_bytes)} ·{" "}
            <span
              className="status-badge"
              data-status={bundle.desensitized ? "succeeded" : "failed"}
            >
              {bundle.desensitized ? "已脱敏" : "未脱敏"}
            </span>
          </p>
        </div>
      )}
    </section>
  );
}
