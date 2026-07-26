/**
 * 创作页子视图 · 04 视频草稿
 * 真实接入 useRenderStore.render / cancel / retry → CreateRenderJob / CancelJob 命令。
 *
 * 数据流：
 *   - useRenderStore.sourceVersionId 由 CreateScriptView 在脚本保存后写入
 *   - 用户选择 TTS 引擎 → setTtsEngine
 *   - 用户点击「开始渲染」→ render() → status + progress + draft
 *   - 渲染中可取消 → cancel()
 *   - 失败可重试 → retry()
 */

import { useRenderStore, type RenderStatus } from "@/stores/useRenderStore";
import { useViewStore } from "@/stores/useViewStore";

function statusLabel(s: RenderStatus): string {
  switch (s) {
    case "idle": return "尚未渲染";
    case "running": return "渲染中";
    case "succeeded": return "渲染完成";
    case "failed": return "失败";
    case "cancelled": return "已取消";
    default: return s;
  }
}

function statusClass(s: RenderStatus): string {
  switch (s) {
    case "running": return "ai";
    case "succeeded": return "success";
    case "failed": return "danger";
    case "cancelled": return "warning";
    default: return "warning";
  }
}

export function CreateRenderView() {
  const setView = useViewStore((s) => s.setView);
  const sourceVersionId = useRenderStore((s) => s.sourceVersionId);
  const template = useRenderStore((s) => s.template);
  const ttsEngine = useRenderStore((s) => s.ttsEngine);
  const status = useRenderStore((s) => s.status);
  const jobId = useRenderStore((s) => s.jobId);
  const progress = useRenderStore((s) => s.progress);
  const draft = useRenderStore((s) => s.draft);
  const isBusy = useRenderStore((s) => s.isBusy);
  const error = useRenderStore((s) => s.error);
  const setTemplate = useRenderStore((s) => s.setTemplate);
  const setTtsEngine = useRenderStore((s) => s.setTtsEngine);
  const render = useRenderStore((s) => s.render);
  const cancel = useRenderStore((s) => s.cancel);
  const retry = useRenderStore((s) => s.retry);

  const canRender = !!sourceVersionId && !isBusy;
  const progressPercent = Math.round(progress * 100);

  return (
    <>
      <section className="page-head" data-od-id="render-heading">
        <div>
          <p className="eyebrow">DRAFT RENDER · 9:16</p>
          <h1>先生成可审阅的视频草稿</h1>
          <p className="page-subtitle">
            {sourceVersionId
              ? `基于版本 ${sourceVersionId.slice(0, 8)} 渲染竖屏视频草稿。`
              : "请先在「脚本创作」步骤保存脚本，再回到此页渲染。"}
          </p>
        </div>
        <span className={`status ${statusClass(status)}`}>
          {statusLabel(status)}
        </span>
      </section>

      <section className="render-layout" data-od-id="render-workspace">
        <article className="panel">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">9:16 预览</h2>
              <div className="panel-meta">
                {draft ? `${draft.resolution[0]} × ${draft.resolution[1]} · ${draft.fps}fps` : "1080 × 1920 · 30fps"}
              </div>
            </div>
            <span className={`status ${statusClass(status)}`}>{statusLabel(status)}</span>
          </div>
          <div className="panel-body">
            <div className="phone-preview" id="phonePreview">
              <div className="preview-copy">
                <p className="eyebrow">视频草稿</p>
                <h2>
                  {status === "succeeded" && draft?.video_uri
                    ? "草稿已生成"
                    : status === "running"
                      ? `渲染中 ${progressPercent}%`
                      : "尚未渲染"}
                </h2>
                <p>
                  {status === "succeeded" && draft
                    ? `模板：${draft.template} · TTS：${draft.tts_engine}`
                    : "配置语音与字幕后点击「开始渲染」。"}
                </p>
                {draft?.video_uri && (
                  <p className="panel-meta mono" style={{ marginTop: 8, fontSize: 11 }}>
                    {draft.video_uri}
                  </p>
                )}
              </div>
            </div>
          </div>
        </article>

        <div className="render-controls">
          <article className="panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">语音与字幕</h2>
                <div className="panel-meta">选择 TTS 引擎或上传用户录音</div>
              </div>
            </div>
            <div className="panel-body grid">
              <div className="form-group">
                <label htmlFor="ttsEngine">语音来源</label>
                <select
                  id="ttsEngine"
                  className="select"
                  value={ttsEngine}
                  onChange={(e) => setTtsEngine(e.target.value as "synthesize" | "user_audio")}
                  disabled={isBusy}
                >
                  <option value="synthesize">Edge TTS · 合成语音</option>
                  <option value="user_audio">用户录音</option>
                </select>
              </div>
              <div className="form-group">
                <label htmlFor="template">模板</label>
                <select
                  id="template"
                  className="select"
                  value={template}
                  onChange={(e) => setTemplate(e.target.value)}
                  disabled={isBusy}
                >
                  <option value="vertical-caption-v1">竖屏字幕 v1</option>
                  <option value="vertical-story-v1">竖屏故事 v1</option>
                </select>
              </div>
            </div>
          </article>

          <article className="panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">渲染任务</h2>
                <div className="panel-meta" id="renderSummary">
                  {status === "running"
                    ? "正在渲染…"
                    : status === "succeeded"
                      ? "已完成"
                      : status === "failed"
                        ? "渲染失败"
                        : status === "cancelled"
                          ? "已取消"
                          : "等待开始"}
                </div>
              </div>
              <span className="mono panel-meta" id="renderPercent">
                {progressPercent}%
              </span>
            </div>
            <div className="panel-body">
              <div
                className="progress"
                id="renderProgress"
                style={{ ["--progress" as string]: `${progressPercent}%` }}
              >
                <span />
              </div>
              <dl className="provenance section-gap">
                <div className="provenance-row">
                  <dt>源版本</dt>
                  <dd className="mono">
                    {sourceVersionId ? sourceVersionId.slice(0, 16) + "…" : "未设置"}
                  </dd>
                </div>
                <div className="provenance-row">
                  <dt>模板</dt>
                  <dd>{template}</dd>
                </div>
                <div className="provenance-row">
                  <dt>TTS</dt>
                  <dd>{ttsEngine === "synthesize" ? "Edge TTS" : "用户录音"}</dd>
                </div>
                {jobId && (
                  <div className="provenance-row">
                    <dt>job_id</dt>
                    <dd className="mono">{jobId.slice(0, 16)}…</dd>
                  </div>
                )}
              </dl>
              <div className="task-actions">
                {status === "running" && (
                  <button
                    className="btn small ghost"
                    type="button"
                    onClick={() => void cancel()}
                  >
                    取消
                  </button>
                )}
                {status === "failed" && (
                  <button
                    className="btn small"
                    type="button"
                    onClick={() => void retry()}
                  >
                    从失败重试
                  </button>
                )}
                <a
                  className="btn small ghost"
                  onClick={() => setView("tasks")}
                  style={{ cursor: "pointer" }}
                >
                  查看任务
                </a>
              </div>
            </div>
          </article>

          <article className="panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">输出 Artifact</h2>
                <div className="panel-meta">渲染完成后可用</div>
              </div>
              <span className={`status ${status === "succeeded" ? "success" : "warning"}`}>
                {status === "succeeded" ? "可导出" : "等待渲染"}
              </span>
            </div>
            <div className="panel-body export-list">
              <div className="export-row">
                <div>
                  <strong>视频草稿 MP4</strong>
                  <div className="row-sub">
                    {draft ? `${draft.resolution[0]} × ${draft.resolution[1]}` : "1080 × 1920 · H.264"}
                  </div>
                </div>
                <button
                  className="btn small"
                  type="button"
                  disabled={status !== "succeeded" || !draft?.video_uri}
                  onClick={() => {
                    if (draft?.video_uri) {
                      void navigator.clipboard.writeText(draft.video_uri);
                    }
                  }}
                >
                  复制路径
                </button>
              </div>
            </div>
          </article>
        </div>
      </section>

      {error && (
        <p className="error-text" style={{ color: "var(--danger)", marginTop: 12 }}>
          {error}
        </p>
      )}

      <div className="inline-actions" style={{ marginTop: 18 }}>
        <button
          type="button"
          className="btn ghost"
          onClick={() => useViewStore.getState().setCreateSubView("script")}
        >
          返回脚本
        </button>
        <button
          type="button"
          className="btn primary"
          id="renderButton"
          disabled={!canRender}
          onClick={() => void render()}
        >
          {isBusy ? "渲染中…" : status === "succeeded" ? "重新渲染" : "开始渲染"}
        </button>
      </div>
    </>
  );
}
