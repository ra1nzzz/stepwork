/**
 * 内容分析视图（W4 Batch3）
 * provider-switch（cloud / openai-compatible / ollama）+ 失败重试
 */

import { useAnalysisStore } from "@/stores/useAnalysisStore";
import type { ProviderKind } from "@/lib/types";

const KINDS: { kind: ProviderKind; label: string }[] = [
  { kind: "cloud", label: "云端 (Cloud)" },
  { kind: "openai-compatible", label: "OpenAI 兼容" },
  { kind: "ollama", label: "Ollama (本地)" },
];

export function AnalysisView() {
  const provider = useAnalysisStore((s) => s.provider);
  const reports = useAnalysisStore((s) => s.reports);
  const isBusy = useAnalysisStore((s) => s.isBusy);
  const error = useAnalysisStore((s) => s.error);
  const setKind = useAnalysisStore((s) => s.setProviderKind);
  const setField = useAnalysisStore((s) => s.setProviderField);
  const analyze = useAnalysisStore((s) => s.analyze);
  const reset = useAnalysisStore((s) => s.reset);

  return (
    <section className="feature-view" data-od-id="analysis-view">
      <header className="feature-head">
        <h1>内容分析</h1>
        <p className="feature-sub">选择 AI Provider，对转写发起结构化分析</p>
      </header>

      <div className="provider-switch" data-od-id="provider-switch">
        {KINDS.map((k) => (
          <button
            key={k.kind}
            type="button"
            className={`chip${provider.kind === k.kind ? " active" : ""}`}
            onClick={() => setKind(k.kind)}
          >
            {k.label}
          </button>
        ))}
      </div>

      <div className="provider-form">
        <label>
          Base URL
          <input
            value={provider.base_url}
            onChange={(e) => setField("base_url", e.target.value)}
            placeholder="https://..."
          />
        </label>
        <label>
          Model
          <input
            value={provider.model}
            onChange={(e) => setField("model", e.target.value)}
            placeholder="model name"
          />
        </label>
        {provider.kind !== "ollama" && (
          <label>
            API Key
            <input
              type="password"
              value={provider.api_key}
              onChange={(e) => setField("api_key", e.target.value)}
              placeholder="sk-..."
            />
          </label>
        )}
      </div>

      <div className="analysis-actions">
        <button
          type="button"
          className="btn primary"
          disabled={isBusy}
          onClick={() => void analyze(`cv-${Date.now()}`)}
        >
          {isBusy ? "分析中…" : "发起分析"}
        </button>
        {reports.length > 0 && (
          <button type="button" className="btn ghost" onClick={() => reset()}>
            清空
          </button>
        )}
      </div>

      {error && (
        <p className="error-text" data-od-id="analysis-error">
          {error}
        </p>
      )}

      <ul className="report-list">
        {reports.map((r, i) => (
          <li key={i} className="report-item" data-od-id={`report-${i}`}>
            <div className="report-head">
              <span className="status-badge" data-status={r.status}>
                {r.status}
              </span>
              <span className="report-provider">{r.provider ?? "—"}</span>
            </div>
            {r.error && <p className="error-text">{r.error}</p>}
            {r.summary && <p className="report-summary">{r.summary}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}
