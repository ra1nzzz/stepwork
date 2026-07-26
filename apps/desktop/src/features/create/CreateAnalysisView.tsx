/**
 * 创作页子视图 · 01 素材分析（底部：转写 + 结构化分析）
 * 真实接入 useTranscriptStore → TranscribeSource，useAnalysisStore → AnalyzeSource。
 *
 * 数据流：
 *   1. 用户从 useImportStore.assets 选一个 asset
 *   2. 调 useTranscriptStore.transcribe(assetId) → 得到 versionId
 *   3. 调 useAnalysisStore.analyze(versionId) → 完整结构化报告
 *      （summary/hook/structure/topics/key_points/risks/… 经 GetContentVersion 拉取）
 *   4. 把 transcript versionId 写入 useScriptStore.setSourceVersion，供下游角度生成
 *
 * Tranche 2：
 *   - 逐字稿全文：GetContentVersion（store 有 version id）；缺失时回退
 *     ListContentVersions contentType='transcript' 最新一条
 *   - 编辑模式：字段编辑 → SaveAnalysis 生成新版本（版本链，不覆盖历史）
 *   - 费用透明：执行前展示 provider/model/预计费用，执行后展示 detail.invocation
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useImportStore } from "@/stores/useImportStore";
import { useTranscriptStore, type TranscriptJob } from "@/stores/useTranscriptStore";
import { useAnalysisStore } from "@/stores/useAnalysisStore";
import { useScriptStore } from "@/stores/useScriptStore";
import { useViewStore } from "@/stores/useViewStore";
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "@/lib/tauri";
import { estimateCost, formatCost, useProviderInfo } from "@/lib/useProviderInfo";
import type {
  AnalysisMode,
  AnalysisReportData,
  ContentVersionDetail,
  ContentVersionSummary,
} from "@/lib/types";

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

function sentimentLabel(s: string | null): string {
  if (s === "positive") return "正面";
  if (s === "negative") return "负面";
  if (s === "neutral") return "中性";
  return s ?? "未知";
}

/** 编辑模式表单模型（数组字段按行编辑） */
interface EditModel {
  summary: string;
  hook: string;
  structureText: string;
  topicsText: string;
  keyPointsText: string;
  risksText: string;
  sentiment: string;
  suggestedTitle: string;
  suggestedTagsText: string;
}

function toEditModel(d: AnalysisReportData): EditModel {
  return {
    summary: d.summary,
    hook: d.hook ?? "",
    structureText: d.structure.join("\n"),
    topicsText: d.topics.join("\n"),
    keyPointsText: d.key_points.join("\n"),
    risksText: d.risks.join("\n"),
    sentiment: d.sentiment ?? "neutral",
    suggestedTitle: d.suggested_title ?? "",
    suggestedTagsText: d.suggested_tags.join("\n"),
  };
}

function splitLines(text: string): string[] {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function fromEditModel(m: EditModel, base: AnalysisReportData): AnalysisReportData {
  return {
    ...base,
    summary: m.summary,
    hook: m.hook.trim() ? m.hook.trim() : null,
    structure: splitLines(m.structureText),
    topics: splitLines(m.topicsText),
    key_points: splitLines(m.keyPointsText),
    risks: splitLines(m.risksText),
    sentiment: m.sentiment || null,
    suggested_title: m.suggestedTitle.trim() ? m.suggestedTitle.trim() : null,
    suggested_tags: splitLines(m.suggestedTagsText),
  };
}

/** 报告只读区块（标题 + 列表/段落） */
function ReportSection({
  title,
  items,
  text,
}: {
  title: string;
  items?: string[];
  text?: string | null;
}) {
  if ((items == null || items.length === 0) && !text) return null;
  return (
    <div className="report-section" style={{ marginBottom: 12 }}>
      <h3>{title}</h3>
      {text && <p>{text}</p>}
      {items && items.length > 0 && (
        <ul className="report-list">
          {items.map((it, i) => (
            <li key={i}>{it}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function CreateAnalysisView() {
  const setCreateSubView = useViewStore((s) => s.setCreateSubView);
  const selectedProjectId = useViewStore((s) => s.selectedProjectId);
  const assets = useImportStore((s) => s.assets);

  const jobs = useTranscriptStore((s) => s.jobs);
  const isTranscribing = useTranscriptStore((s) => s.isBusy);
  const transcribeError = useTranscriptStore((s) => s.error);
  const transcribe = useTranscriptStore((s) => s.transcribe);
  const cancelTranscribe = useTranscriptStore((s) => s.cancel);
  const retryTranscribe = useTranscriptStore((s) => s.retry);

  const reports = useAnalysisStore((s) => s.reports);
  const isAnalyzing = useAnalysisStore((s) => s.isBusy);
  const isSaving = useAnalysisStore((s) => s.isSaving);
  const analysisError = useAnalysisStore((s) => s.error);
  const analyze = useAnalysisStore((s) => s.analyze);
  const saveAnalysis = useAnalysisStore((s) => s.saveAnalysis);

  const setScriptSourceVersion = useScriptStore((s) => s.setSourceVersion);

  const providerInfo = useProviderInfo();

  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(
    assets[0]?.id ?? null,
  );

  // 逐字稿全文（GetContentVersion 拉取）
  const [transcriptText, setTranscriptText] = useState<string | null>(null);
  const [transcriptExpanded, setTranscriptExpanded] = useState(false);

  // 分析模式（PRD 8.2「选择快速或精确分析」）：精确需媒体源 + 可用 ffmpeg
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>("quick");

  // 编辑模式
  const [editModel, setEditModel] = useState<EditModel | null>(null);
  // PRD-WS-005：分析报告编辑的防抖自动保存（与脚本编辑器同节奏 800ms）
  const autoSaveTimer = useRef<number | null>(null);
  const [autoSaveLabel, setAutoSaveLabel] = useState<string | null>(null);
  useEffect(
    () => () => {
      if (autoSaveTimer.current) window.clearTimeout(autoSaveTimer.current);
    },
    [],
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

  // 逐字稿全文：优先按 store 里的 version id 拉取；
  // 缺失时回退 ListContentVersions contentType='transcript' 最新一条
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        let versionId = latestTranscriptVersion;
        if (!versionId && selectedProjectId) {
          const listEnv = buildEnvelope(
            "ListContentVersions",
            getWorkspaceId(),
            selectedProjectId,
            { projectId: selectedProjectId, contentType: "transcript", limit: 1 },
          );
          const listRes = await dispatchCommand(listEnv);
          if (listRes.ok) {
            const detail = (listRes.detail ?? {}) as {
              versions?: ContentVersionSummary[];
            };
            versionId = detail.versions?.[0]?.id ?? null;
          }
        }
        if (!versionId) {
          if (!cancelled) setTranscriptText(null);
          return;
        }
        const env = buildEnvelope("GetContentVersion", getWorkspaceId(), selectedProjectId, {
          versionId,
        });
        const res = await dispatchCommand(env);
        if (cancelled || !res.ok) return;
        const detail = (res.detail ?? {}) as { version?: ContentVersionDetail };
        setTranscriptText(detail.version?.content ?? null);
      } catch {
        /* 后端未连接：不展示全文 */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [latestTranscriptVersion, selectedProjectId]);

  const latestReport = reports[reports.length - 1] ?? null;
  const reportData = latestReport?.data ?? null;

  // 执行前费用粗估：转写字符量 / 1000 × costPer1k
  const preCost = useMemo(() => {
    if (!transcriptText || !providerInfo) return null;
    return estimateCost(transcriptText.length, providerInfo.costPer1k);
  }, [transcriptText, providerInfo]);

  function handleTranscribe() {
    if (!selectedAssetId) return;
    void transcribe(selectedAssetId);
  }

  function handleAnalyze() {
    if (!latestTranscriptVersion) return;
    setEditModel(null);
    // 精确模式（PRD-ANA-003）带上媒体源素材，后端据此做场景切分/关键帧
    void analyze(latestTranscriptVersion, undefined, {
      mode: analysisMode,
      assetId: selectedAssetId,
    });
  }

  function enterEdit() {
    if (reportData) setEditModel(toEditModel(reportData));
  }

  async function handleSaveEdit() {
    if (!editModel || !reportData) return;
    if (autoSaveTimer.current) window.clearTimeout(autoSaveTimer.current);
    await saveAnalysis(fromEditModel(editModel, reportData));
    setAutoSaveLabel("已保存");
    setEditModel(null);
  }

  /**
   * PRD-WS-005「自动保存和版本恢复：异常退出后正文丢失不超过最近一次保存周期」。
   * 此前只有脚本编辑器有 800ms 防抖自动保存，分析报告编辑必须手动点保存 ——
   * 编辑到一半崩溃即全部丢失。这里对齐脚本编辑器的节奏。
   */
  function patchEdit(patch: Partial<EditModel>) {
    setEditModel((prev) => {
      if (!prev) return prev;
      const next = { ...prev, ...patch };
      if (autoSaveTimer.current) window.clearTimeout(autoSaveTimer.current);
      setAutoSaveLabel("正在保存…");
      autoSaveTimer.current = window.setTimeout(() => {
        if (reportData) {
          void saveAnalysis(fromEditModel(next, reportData)).then(() =>
            setAutoSaveLabel("已保存"),
          );
        }
      }, 800);
      return next;
    });
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
        {/* 左：转写任务 + 逐字稿全文 */}
        <div className="grid">
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

          {/* 逐字稿全文（GetContentVersion 拉取） */}
          <article className="panel" data-od-id="transcript-fulltext-panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">逐字稿全文</h2>
                <div className="panel-meta">
                  {transcriptText
                    ? `${transcriptText.length} 字`
                    : "转写完成后自动加载"}
                </div>
              </div>
              {transcriptText && transcriptText.length > 600 && (
                <button
                  className="btn small ghost"
                  type="button"
                  onClick={() => setTranscriptExpanded((v) => !v)}
                >
                  {transcriptExpanded ? "收起" : "展开全文"}
                </button>
              )}
            </div>
            <div className="panel-body">
              {transcriptText ? (
                <p
                  className="transcript"
                  style={{ margin: 0, whiteSpace: "pre-wrap", lineHeight: 1.8 }}
                >
                  {transcriptExpanded || transcriptText.length <= 600
                    ? transcriptText
                    : `${transcriptText.slice(0, 600)}…`}
                </p>
              ) : (
                <p className="panel-meta" style={{ margin: 0 }}>
                  尚无逐字稿。完成转写后此处展示全文。
                </p>
              )}
            </div>
          </article>
        </div>

        {/* 右：结构化分析（完整报告 + 编辑模式） */}
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

          {/* 分析模式切换（PRD 8.2 / PRD-ANA-003） */}
          <div className="panel-body" style={{ paddingBottom: 0 }}>
            <div className="segmented" role="group" aria-label="分析模式">
              <button
                type="button"
                className={`btn small ${analysisMode === "quick" ? "primary" : ""}`}
                onClick={() => setAnalysisMode("quick")}
                disabled={isAnalyzing}
                aria-pressed={analysisMode === "quick"}
              >
                快速分析
              </button>
              <button
                type="button"
                className={`btn small ${analysisMode === "precise" ? "primary" : ""}`}
                onClick={() => setAnalysisMode("precise")}
                disabled={isAnalyzing || !selectedAssetId}
                aria-pressed={analysisMode === "precise"}
                title={
                  selectedAssetId
                    ? "结合场景切分与关键帧，需本机可用的 ffmpeg"
                    : "请先选择素材"
                }
              >
                精确分析
              </button>
            </div>
            <p className="panel-meta" style={{ marginTop: 8 }}>
              {analysisMode === "precise"
                ? "精确模式：结合转写稿与视频场景切分/关键帧时间线（需 ffmpeg）。"
                : "快速模式：仅基于转写文本分析。"}
            </p>
          </div>

          {/* 费用透明：执行前 provider/model/预计费用（PRD-ANA-006） */}
          <div className="panel-body provenance-bar" style={{ paddingBottom: 0 }}>
            <dl className="provenance">
              {latestReport && latestReport.mode === "precise" && (
                <div className="provenance-row">
                  <dt>场景切分</dt>
                  <dd>{latestReport.sceneCount} 个场景（含关键帧时间戳）</dd>
                </div>
              )}
              <div className="provenance-row">
                <dt>将使用模型</dt>
                <dd className="mono">
                  {providerInfo
                    ? `${providerInfo.ai.provider ?? "未配置"} / ${providerInfo.ai.model ?? "未配置"}`
                    : "读取配置中…"}
                </dd>
              </div>
              <div className="provenance-row">
                <dt>预计费用</dt>
                <dd>
                  {transcriptText
                    ? `${formatCost(preCost)}（${transcriptText.length} 字粗估）`
                    : "待转写完成"}
                </dd>
              </div>
              {latestReport?.invocation && (
                <div className="provenance-row">
                  <dt>本次调用</dt>
                  <dd>
                    {latestReport.invocation.provider} / {latestReport.invocation.model}
                    {" · "}
                    {formatCost(latestReport.invocation.estimated_cost)}
                  </dd>
                </div>
              )}
            </dl>
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

            {latestReport?.status === "succeeded" && !reportData && (
              <p className="panel-meta" style={{ margin: 0 }}>
                分析完成，但报告全文拉取失败。版本
                {latestReport.versionId ? ` ${latestReport.versionId.slice(0, 8)}` : ""}
                已落库。
              </p>
            )}

            {/* 只读完整报告 */}
            {latestReport?.status === "succeeded" && reportData && !editModel && (
              <>
                <ReportSection title="核心判断" text={reportData.summary} />
                <ReportSection title="开头钩子" text={reportData.hook} />
                <ReportSection title="内容结构" items={reportData.structure} />
                <ReportSection title="话题点" items={reportData.topics} />
                <ReportSection title="关键要点" items={reportData.key_points} />
                <ReportSection title="风险点" items={reportData.risks} />
                <ReportSection
                  title="情感倾向"
                  text={sentimentLabel(reportData.sentiment)}
                />
                <ReportSection title="建议标题" text={reportData.suggested_title} />
                {reportData.suggested_tags.length > 0 && (
                  <div className="report-section" style={{ marginBottom: 12 }}>
                    <h3>建议标签</h3>
                    <div className="filters" style={{ flexWrap: "wrap" }}>
                      {reportData.suggested_tags.map((t) => (
                        <span key={t} className="chip">
                          {t}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {reportData.confidence != null && (
                  <p className="panel-meta" style={{ marginTop: 8 }}>
                    置信度 {(reportData.confidence * 100).toFixed(1)}%
                    {latestReport.versionId
                      ? ` · 版本 ${latestReport.versionId.slice(0, 8)}`
                      : ""}
                  </p>
                )}
                <div className="inline-actions section-gap">
                  <button className="btn small ghost" type="button" onClick={enterEdit}>
                    编辑报告
                  </button>
                </div>
              </>
            )}

            {/* 编辑模式：字段编辑 → SaveAnalysis 生成新版本 */}
            {editModel && reportData && (
              <>
                <div className="form-group" style={{ marginBottom: 10 }}>
                  <label htmlFor="editSummary">核心判断</label>
                  <textarea
                    id="editSummary"
                    className="field"
                    rows={3}
                    style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
                    value={editModel.summary}
                    onChange={(e) => patchEdit({ summary: e.target.value })}
                  />
                </div>
                <div className="form-group" style={{ marginBottom: 10 }}>
                  <label htmlFor="editHook">开头钩子</label>
                  <textarea
                    id="editHook"
                    className="field"
                    rows={2}
                    style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
                    value={editModel.hook}
                    onChange={(e) => patchEdit({ hook: e.target.value })}
                  />
                </div>
                <div className="form-group" style={{ marginBottom: 10 }}>
                  <label htmlFor="editStructure">内容结构（每行一条）</label>
                  <textarea
                    id="editStructure"
                    className="field"
                    rows={4}
                    style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
                    value={editModel.structureText}
                    onChange={(e) =>
                      patchEdit({ structureText: e.target.value })
                    }
                  />
                </div>
                <div className="form-group" style={{ marginBottom: 10 }}>
                  <label htmlFor="editTopics">话题点（每行一条）</label>
                  <textarea
                    id="editTopics"
                    className="field"
                    rows={4}
                    style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
                    value={editModel.topicsText}
                    onChange={(e) =>
                      patchEdit({ topicsText: e.target.value })
                    }
                  />
                </div>
                <div className="form-group" style={{ marginBottom: 10 }}>
                  <label htmlFor="editKeyPoints">关键要点（每行一条）</label>
                  <textarea
                    id="editKeyPoints"
                    className="field"
                    rows={4}
                    style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
                    value={editModel.keyPointsText}
                    onChange={(e) =>
                      patchEdit({ keyPointsText: e.target.value })
                    }
                  />
                </div>
                <div className="form-group" style={{ marginBottom: 10 }}>
                  <label htmlFor="editRisks">风险点（每行一条）</label>
                  <textarea
                    id="editRisks"
                    className="field"
                    rows={3}
                    style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
                    value={editModel.risksText}
                    onChange={(e) =>
                      patchEdit({ risksText: e.target.value })
                    }
                  />
                </div>
                <div className="form-group" style={{ marginBottom: 10 }}>
                  <label htmlFor="editSentiment">情感倾向</label>
                  <select
                    id="editSentiment"
                    className="select"
                    value={editModel.sentiment}
                    onChange={(e) =>
                      patchEdit({ sentiment: e.target.value })
                    }
                  >
                    <option value="positive">正面</option>
                    <option value="neutral">中性</option>
                    <option value="negative">负面</option>
                  </select>
                </div>
                <div className="form-group" style={{ marginBottom: 10 }}>
                  <label htmlFor="editTitle">建议标题</label>
                  <input
                    id="editTitle"
                    className="field"
                    style={{ width: "100%" }}
                    value={editModel.suggestedTitle}
                    onChange={(e) =>
                      patchEdit({ suggestedTitle: e.target.value })
                    }
                  />
                </div>
                <div className="form-group" style={{ marginBottom: 10 }}>
                  <label htmlFor="editTags">建议标签（每行一条）</label>
                  <textarea
                    id="editTags"
                    className="field"
                    rows={3}
                    style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
                    value={editModel.suggestedTagsText}
                    onChange={(e) =>
                      patchEdit({ suggestedTagsText: e.target.value })
                    }
                  />
                </div>
                <div className="inline-actions">
                  <button
                    className="btn small ghost"
                    type="button"
                    onClick={() => setEditModel(null)}
                    disabled={isSaving}
                  >
                    取消
                  </button>
                  <button
                    className="btn small primary"
                    type="button"
                    onClick={() => void handleSaveEdit()}
                    disabled={isSaving}
                  >
                    {isSaving ? "保存中…" : "保存为新版本"}
                  </button>
                  {/* PRD-WS-005：自动保存状态可见，用户知道内容不会丢 */}
                  {autoSaveLabel && (
                    <span className="panel-meta" style={{ alignSelf: "center" }}>
                      {autoSaveLabel}（编辑后 0.8 秒自动保存）
                    </span>
                  )}
                </div>
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
