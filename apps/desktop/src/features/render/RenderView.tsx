/**
 * 视频草稿渲染视图（W6 Batch1）
 * - 选渲染源版本（默认 mock 的 script 版）/ 模板 / TTS 引擎
 * - 渲染（CreateRenderJob）→ 取消（CancelJob）→ 失败重试
 * - 展示产物 video_uri（对齐 PRD §338 的 阶段/进度/取消/重试）
 */

import { useRenderStore } from "@/stores/useRenderStore";

const TEMPLATES = ["vertical-caption-v1"] as const;

export function RenderView() {
  const sourceVersionId = useRenderStore((s) => s.sourceVersionId);
  const template = useRenderStore((s) => s.template);
  const ttsEngine = useRenderStore((s) => s.ttsEngine);
  const status = useRenderStore((s) => s.status);
  const jobId = useRenderStore((s) => s.jobId);
  const draft = useRenderStore((s) => s.draft);
  const error = useRenderStore((s) => s.error);
  const isBusy = useRenderStore((s) => s.isBusy);

  const setSourceVersion = useRenderStore((s) => s.setSourceVersion);
  const setTemplate = useRenderStore((s) => s.setTemplate);
  const setTtsEngine = useRenderStore((s) => s.setTtsEngine);
  const render = useRenderStore((s) => s.render);
  const cancel = useRenderStore((s) => s.cancel);
  const retry = useRenderStore((s) => s.retry);
  const reset = useRenderStore((s) => s.reset);

  return (
    <section className="feature-view" data-od-id="render-view">
      <header className="feature-head">
        <h2>视频草稿渲染</h2>
        <p className="feature-sub">基于脚本/转写生成短视频草稿（竖版字幕）</p>
      </header>

      <div className="render-form">
        <label>
          渲染源版本 id
          <input
            value={sourceVersionId}
            onChange={(e) => setSourceVersion(e.target.value)}
            placeholder="cv-script-local"
          />
        </label>
        <label>
          模板
          <select value={template} onChange={(e) => setTemplate(e.target.value)}>
            {TEMPLATES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label>
          TTS 引擎
          <select
            value={ttsEngine}
            onChange={(e) => setTtsEngine(e.target.value as "synthesize" | "user_audio")}
          >
            <option value="synthesize">合成（本地/云 TTS）</option>
            <option value="user_audio">用户录音</option>
          </select>
        </label>
      </div>

      <div className="render-actions">
        <button
          type="button"
          className="btn primary"
          disabled={isBusy}
          onClick={() => void render()}
        >
          {status === "running" ? "渲染中…" : "开始渲染"}
        </button>
        <button
          type="button"
          className="btn"
          disabled={isBusy || !jobId}
          onClick={() => void cancel()}
        >
          取消
        </button>
        {(status === "failed" || status === "cancelled") && (
          <button type="button" className="btn ghost" onClick={() => void retry()}>
            重试
          </button>
        )}
        {draft && (
          <button type="button" className="btn ghost" onClick={() => reset()}>
            清空
          </button>
        )}
      </div>

      <div className="render-status">
        <span className="status-badge" data-status={status}>
          {status}
        </span>
        {jobId && <span className="render-job">job {jobId.slice(0, 8)}</span>}
      </div>

      {error && (
        <p className="error-text" data-od-id="render-error">
          {error}
        </p>
      )}

      {draft && (
        <div className="draft-card" data-od-id="draft-card">
          <h3>渲染产物</h3>
          <p>
            模板：{draft.template} · 引擎：{draft.tts_engine} ·{" "}
            {draft.resolution[0]}×{draft.resolution[1]}@{draft.fps}fps
          </p>
          <p className="draft-uri">{draft.video_uri}</p>
        </div>
      )}
    </section>
  );
}
