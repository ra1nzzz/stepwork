/**
 * 创作页子视图 · 01 素材分析（底部：转写 + 结构化分析）
 * 真实接入 useTranscriptStore → TranscribeSource，useAnalysisStore → AnalyzeSource。
 *
 * 数据流：
 *   1. 用户从 useImportStore.assets 选一个 asset
 *   2. 调 useTranscriptStore.transcribe(assetId) → 得到 versionId
 *   3. 调 useAnalysisStore.analyze(versionId) → 得到结构化报告
 *   4. 把 transcript versionId 写入 useScriptStore.setSourceVersion，供下游角度生成
 */

import { useEffect, useMemo, useState } from "react";
import { useImportStore } from "@/stores/useImportStore";
import { useTranscriptStore, type TranscriptJob } from "@/stores/useTranscriptStore";
import { useAnalysisStore } from "@/stores/useAnalysisStore";
import { useScriptStore } from "@/stores/useScriptStore";
import { useViewStore } from "@/stores/useViewStore";

function jobStatusLabel(s: TranscriptJob["status"]): string {
  switch (s) {
    case "pending": return "等待中";
    case "running": return "转写中";
    case "succeeded": return "完成";
    case "failed": return "失败";
    case "cancelled": return "已取消";
    default: return s;
  }
}

function jobStatusClass(s: TranscriptJob["status"]): string {
  switch (s) {
    case "running":
    case "pending": return "ai";
    case "succeeded": return "success";
    case "failed": return "danger";
    case "cancelled": return "warning";
    default: return "";
  }
}

export function CreateAnalysisView() {
  const setCreateSubView = useViewStore((s) => s.setCreateSubView);
  const assets = useImportStore((s) => s.assets);

  const jobs = useTranscriptStore((s) => s.jobs);
  const isTranscribing = useTranscriptStore((s) => s.isBusy);
  const transcribeError = useTranscriptStore((s) => s.error);
  const transcribe = useTranscriptStore((s) => s.transcribe);
  const cancelTranscribe = useTranscriptStore((s) => s.cancel);
  const retryTranscribe = useTranscriptStore((s) => s.retry);

  const reports = useAnalysisStore((s) => s.reports);
  const isAnalyzing = useAnalysisStore((s) => s.isBusy);
  const analysisError = useAnalysisStore((s) => s.error);
  const analyze = useAnalysisStore((s) => s.analyze);

  const setScriptSourceVersion = useScriptStore((s) => s.setSourceVersion);

  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(
    assets[0]?.id ?? null,
  );

  // 自动选中第一个 asset
  useEffect(() => {
    if (!selectedAssetId && assets.length > 0) {
      setSelectedAssetId(assets[0].id);
    }
  }, [assets, selectedAssetId]);

  // 把最新成功的 transcript versionId 写入 scriptStore，供下游使用
  const latestTranscriptVersion = useMemo(() => {
    const succeeded = jobs.filter((j) => j.status === "succeeded" && j.versionId);
    return succeeded[succeeded.length - 1]?.versionId ?? null;
  }, [jobs]);

  useEffect(() => {
    if (latestTranscriptVersion) {
      setScriptSourceVersion(latestTranscriptVersion);
    }
  }, [latestTranscriptVersion, setScriptSourceVersion]);

  const latestReport = reports[reports.length - 1] ?? null;

  function handleTranscribe() {
    if (!selectedAssetId) return;
    void transcribe(selectedAssetId);
  }

  function handleAnalyze() {
    if (!latestTranscriptVersion) return;
    void analyze(latestTranscriptVersion);
  }

  return (
    <>
      <section className="page-head" data-od-id="analysis-report-heading">
        <div>
          <p className="eyebrow">ANALYSIS REPORT</p>
          <h1>转写与结构化分析</h1>
          <p className="page-subtitle">
            选择素材进行语音转写，再基于转写结果生成结构化分析。每个步骤都真实调用后端命令。
          </p>
        </div>
        <span
          className={`status ${
            transcribeError || analysisError
              ? "danger"
              : isTranscribing || isAnalyzing
                ? "ai"
                : "success"
          }`}
        >
          {transcribeError || analysisError
            ? "出错"
            : isTranscribing
              ? "转写中"
              : isAnalyzing
                ? "分析中"
                : "就绪"}
        </span>
      </section>

      <section className="layout-main section-gap" id="analysis-report">
        {/* 左：转写任务面板 */}
        <article className="panel" data-od-id="transcript-panel">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">转写任务</h2>
              <div className="panel-meta">
                {assets.length === 0
                  ? "请先在「导入」步骤添加素材"
                  : `可选 ${assets.length} 个素材`}
              </div>
            </div>
            <button
              className="btn small ghost"
              type="button"
              onClick={handleTranscribe}
              disabled={isTranscribing || !selectedAssetId}
            >
              {isTranscribing ? "转写中…" : "开始转写"}
            </button>
          </div>
          <div className="panel-body">
            {/* 素材选择器 */}
            {assets.length > 0 && (
              <div className="form-group" style={{ marginBottom: 16 }}>
                <label htmlFor="assetSelect">选择素材</label>
                <select
                  id="assetSelect"
                  className="select"
                  value={selectedAssetId ?? ""}
                  onChange={(e) => setSelectedAssetId(e.target.value)}
                >
                  {assets.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.local_uri} ({a.kind})
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* 转写任务列表 */}
            {jobs.length === 0 ? (
              <p className="panel-meta" style={{ margin: 0 }}>
                尚无转写任务。选择素材后点击「开始转写」。
              </p>
            ) : (
              <div className="task-list">
                {jobs.map((job) => (
                  <div className="task-item" key={job.id} data-state={job.status}>
                    <div className="task-top">
                      <div>
                        <div className="task-name">
                          {job.assetId ? `asset ${job.assetId.slice(0, 8)}` : "外部素材"}
                        </div>
                        <div className="row-sub">
                          {job.versionId ? `版本 ${job.versionId.slice(0, 8)}` : "无版本"}
                          {job.language ? ` · ${job.language}` : ""}
                        </div>
                      </div>
                      <span className={`status ${jobStatusClass(job.status)}`}>
                        {jobStatusLabel(job.status)}
                      </span>
                    </div>
                    {job.status === "running" && (
                      <div
                        className="progress"
                        style={{ ["--progress" as string]: `${Math.round(job.progress * 100)}%` }}
                      >
                        <span />
                      </div>
                    )}
                    {job.error && (
                      <p className="panel-meta section-gap" style={{ color: "var(--danger)" }}>
                        {job.error}
                      </p>
                    )}
                    <div className="task-actions">
                      {job.status === "failed" && (
                        <button
                          className="btn small retry"
                          type="button"
                          onClick={() => void retryTranscribe(job.id)}
                        >
                          重试
                        </button>
                      )}
                      {(job.status === "running" || job.status === "pending") && (
                        <button
                          className="btn small ghost"
                          type="button"
                          onClick={() => cancelTranscribe(job.id)}
                        >
                          取消
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {transcribeError && (
              <p className="error-text section-gap" style={{ color: "var(--danger)" }}>
                {transcribeError}
              </p>
            )}
          </div>
        </article>

        {/* 右：结构化分析 */}
        <aside className="panel" data-od-id="structured-report-panel">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">结构化分析</h2>
              <div className="panel-meta">
                {latestReport
                  ? `${latestReport.provider ?? "未知"} · ${latestReport.model ?? "未知"}`
                  : "等待转写完成后分析"}
              </div>
            </div>
            <button
              className="btn small primary"
              type="button"
              onClick={handleAnalyze}
              disabled={isAnalyzing || !latestTranscriptVersion}
            >
              {isAnalyzing ? "分析中…" : "生成分析"}
            </button>
          </div>
          <div className="panel-body">
            {!latestReport && !isAnalyzing && (
              <p className="panel-meta" style={{ margin: 0 }}>
                {latestTranscriptVersion
                  ? "点击「生成分析」开始结构化分析。"
                  : "请先完成转写任务。"}
              </p>
            )}

            {(isAnalyzing || latestReport?.status === "pending") && (
              <p className="panel-meta" style={{ margin: 0 }}>
                正在生成分析报告…
              </p>
            )}

            {latestReport?.status === "failed" && (
              <p className="error-text" style={{ color: "var(--danger)" }}>
                {latestReport.error ?? analysisError ?? "分析失败"}
              </p>
            )}

            {latestReport?.status === "succeeded" && (
              <>
                {latestReport.summary && (
                  <div className="report-section" style={{ marginBottom: 12 }}>
                    <h3>核心判断</h3>
                    <p>{latestReport.summary}</p>
                  </div>
                )}
                {latestReport.topics.length > 0 && (
                  <div className="report-section" style={{ marginBottom: 12 }}>
                    <h3>话题点</h3>
                    <ul className="report-list">
                      {latestReport.topics.map((t, i) => (
                        <li key={i}>
                          <strong>{t.title}</strong>
                          {t.summary ? ` — ${t.summary}` : ""}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {latestReport.sentiment && (
                  <div className="report-section">
                    <h3>情感倾向</h3>
                    <p>{latestReport.sentiment}</p>
                  </div>
                )}
                {latestReport.confidence != null && (
                  <p className="panel-meta" style={{ marginTop: 8 }}>
                    置信度 {(latestReport.confidence * 100).toFixed(1)}%
                  </p>
                )}
              </>
            )}
          </div>
          <div className="panel-body" style={{ paddingTop: 0 }}>
            <button
              className="btn primary"
              style={{ width: "100%" }}
              type="button"
              onClick={() => setCreateSubView("angle")}
              disabled={!latestTranscriptVersion}
            >
              生成原创角度
            </button>
          </div>
        </aside>
      </section>
    </>
  );
}
