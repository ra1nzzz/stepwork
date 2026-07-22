/**
 * 转写视图（W3 Batch3）
 * 发起转写，跟踪阶段 / 进度，支持取消与失败重试（PRD §338）
 */

import { useTranscriptStore } from "@/stores/useTranscriptStore";

function statusLabel(s: string): string {
  if (s === "running") return "转写中";
  if (s === "succeeded") return "已完成";
  if (s === "failed") return "失败";
  if (s === "cancelled") return "已取消";
  return "待处理";
}

export function TranscriptView() {
  const jobs = useTranscriptStore((s) => s.jobs);
  const isBusy = useTranscriptStore((s) => s.isBusy);
  const transcribe = useTranscriptStore((s) => s.transcribe);
  const cancel = useTranscriptStore((s) => s.cancel);
  const retry = useTranscriptStore((s) => s.retry);
  const reset = useTranscriptStore((s) => s.reset);

  return (
    <section className="feature-view" data-od-id="transcript-view">
      <header className="feature-head">
        <h1>转写</h1>
        <p className="feature-sub">选择素材发起转写，跟踪阶段 / 进度，失败可重试</p>
      </header>

      <div className="transcript-actions">
        <button
          type="button"
          className="btn primary"
          disabled={isBusy}
          onClick={() => void transcribe(`asset-${Date.now()}`)}
        >
          {isBusy ? "处理中…" : "对示例素材转写"}
        </button>
        {jobs.length > 0 && (
          <button type="button" className="btn ghost" onClick={() => reset()}>
            清空
          </button>
        )}
      </div>

      <ul className="job-list">
        {jobs.map((j) => (
          <li key={j.id} className="job-item" data-od-id={`job-${j.id}`}>
            <div className="job-row">
              <span className="job-id">{j.assetId ?? j.id}</span>
              <span className="status-badge" data-status={j.status}>
                {statusLabel(j.status)}
              </span>
            </div>
            <div className="progress-track" aria-label="进度">
              <div
                className="progress-fill"
                style={{ width: `${Math.round(j.progress * 100)}%` }}
              />
            </div>
            {j.error && <p className="error-text">{j.error}</p>}
            <div className="job-actions">
              {j.status === "running" && (
                <button type="button" className="btn ghost" onClick={() => cancel(j.id)}>
                  取消
                </button>
              )}
              {j.status === "failed" && (
                <button type="button" className="btn primary" onClick={() => void retry(j.id)}>
                  重试
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
