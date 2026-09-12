/**
 * 热点选题面板（S6 前端热点面板）——挂在「02 原创角度」里，作为素材路径之外的
 * 第二条起手线：**从热点起手**。
 *
 * 为什么挂在角度页而不是新开一级导航：短视频的选题来源只有两种，素材分析
 * 与热点推荐，两者的产物都是「一个 `content_version`」，而下游 `GenerateTopic`
 * 吃的是同一个 `source_version_id`。挂在角度页 = 用户在**消费来源**的地方
 * 就能看到另一条来路，不必再记一个新入口（侧栏 7 项是 PRD Ch.7 定的）。
 *
 * 判据层在 `viewModel.ts`（纯函数 + 单测），这里只负责取数与渲染。
 * 硬规矩沿用全仓的：**命令失败必须抛出来并显示**（`runCommand` 已把
 * `ok=false` 一律转成异常），不允许「点了没反应」。
 */

import { useCallback, useEffect, useState } from "react";
import { describeCommandError, runCommand } from "@/lib/useCommand";
import { useScriptStore } from "@/stores/useScriptStore";
import { useViewStore } from "@/stores/useViewStore";
import {
  brandGateNotice,
  breakdownRows,
  candidateLine,
  emptyHint,
  reasonBadge,
  reusedNotice,
  SCORE_FORMULA,
  nextStepHint,
  toBriefView,
  toRecommendView,
  trustBadge,
  type BriefView,
  type RecommendationItem,
  type RecommendView,
} from "./viewModel";

/** 抓取结果摘要（DiscoverHotspotsDetail 的展示子集）。 */
interface DiscoverSummary {
  batchId: string;
  count: number;
  saved: number;
  sources: string[];
  errors: { source: string; error: string }[];
  skipped: { source: string; reason: string }[];
}

function pickText(row: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const v = row[key];
    if (typeof v === "string" && v) return v;
  }
  return "";
}

function sourceLabel(row: Record<string, unknown>): string {
  return pickText(row, ["key", "name", "id", "source", "label"]) || "(未命名源)";
}

function toSummary(detail: Record<string, unknown>): DiscoverSummary {
  const asRows = (v: unknown): Record<string, unknown>[] =>
    Array.isArray(v) ? (v as Record<string, unknown>[]) : [];
  return {
    batchId: typeof detail.batch_id === "string" ? detail.batch_id : "",
    count: typeof detail.count === "number" ? detail.count : 0,
    saved: typeof detail.saved === "number" ? detail.saved : 0,
    sources: Array.isArray(detail.sources)
      ? detail.sources.filter((s): s is string => typeof s === "string")
      : [],
    errors: asRows(detail.errors).map((r) => ({
      source: sourceLabel(r),
      error: pickText(r, ["error", "message", "detail"]) || "未说明原因",
    })),
    skipped: asRows(detail.skipped).map((r) => ({
      source: sourceLabel(r),
      reason: pickText(r, ["reason", "why", "detail"]) || "未说明原因",
    })),
  };
}

export function HotspotPanel() {
  const selectedProjectId = useViewStore((s) => s.selectedProjectId);
  const setSourceVersion = useScriptStore((s) => s.setSourceVersion);
  const setCreateSubView = useViewStore((s) => s.setCreateSubView);

  const [sourceInfo, setSourceInfo] = useState<{ marker: string; count: number } | null>(
    null,
  );
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<DiscoverSummary | null>(null);
  const [view, setView] = useState<RecommendView | null>(null);
  const [brief, setBrief] = useState<BriefView | null>(null);
  const [feedback, setFeedback] = useState<Record<string, string>>({});

  /** 源连接状态：连不上要**教人怎么修**，不能只报个错码。 */
  const loadSources = useCallback(async () => {
    setSourcesError(null);
    try {
      const d = (await runCommand("ListHotspotSources")) as unknown as Record<
        string,
        unknown
      >;
      const sources = Array.isArray(d.sources) ? d.sources : [];
      setSourceInfo({
        marker: typeof d.server_marker === "string" ? d.server_marker : "",
        count: sources.length,
      });
    } catch (e) {
      setSourceInfo(null);
      setSourcesError(describeCommandError(e));
    }
  }, []);

  useEffect(() => {
    void loadSources();
  }, [loadSources]);

  async function discover() {
    setIsBusy(true);
    setError(null);
    setBrief(null);
    try {
      const d = (await runCommand("DiscoverHotspots", {
        limit: 30,
        windowHours: 48,
        save: true,
      })) as unknown as Record<string, unknown>;
      setSummary(toSummary(d));
      // 抓完把推荐清空：那是上一批的判断，留着会让人以为已经刷新过
      setView(null);
    } catch (e) {
      setError(describeCommandError(e));
    } finally {
      setIsBusy(false);
    }
  }

  async function recommend() {
    setIsBusy(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = {
        limit: 10,
        reasonTopN: 5,
        useBrandProfile: true,
      };
      if (summary?.batchId) payload.batchId = summary.batchId;
      const d = (await runCommand(
        "RecommendHotspots",
        payload,
      )) as unknown as Record<string, unknown>;
      setView(toRecommendView(d));
    } catch (e) {
      setError(describeCommandError(e));
    } finally {
      setIsBusy(false);
    }
  }

  async function convert(item: RecommendationItem) {
    setBusyId(item.id);
    setError(null);
    try {
      const payload: Record<string, unknown> = {
        hotspotId: item.id,
        // 理由与来源**原样带入**：转换命令不重算也不代猜，没带就如实记 none
        reason: item.reason,
        reasonSource: item.reasonSource,
        breakdown: item.breakdown,
      };
      if (selectedProjectId) payload.projectId = selectedProjectId;
      const d = (await runCommand(
        "ConvertHotspotToTopic",
        payload,
      )) as unknown as Record<string, unknown>;
      setBrief(toBriefView(d));
    } catch (e) {
      setError(describeCommandError(e));
    } finally {
      setBusyId(null);
    }
  }

  async function sendFeedback(id: string, verdict: "adopted" | "ignored" | "rejected") {
    setBusyId(id);
    setError(null);
    try {
      await runCommand("RecordHotspotFeedback", { hotspotId: id, verdict });
      setFeedback((prev) => ({ ...prev, [id]: verdict }));
    } catch (e) {
      setError(describeCommandError(e));
    } finally {
      setBusyId(null);
    }
  }

  /** 简报 → 角度：把简报版本设成来源版本，切到「02 原创角度」既有流程即可。 */
  function handOffToAngles() {
    if (!brief) return;
    setSourceVersion(brief.contentVersionId);
    setCreateSubView("angle");
  }

  const notice = view ? brandGateNotice(view.brandApplied) : null;
  const nextHint = nextStepHint(brief?.nextStep ?? null);

  return (
    <section className="panel" data-od-id="hotspot-panel">
      <div className="panel-head">
        <div>
          <h2 className="panel-title">或者：从热点起手</h2>
          <div className="panel-meta">
            抓上游热榜 → 按品牌与历史推荐并给出理由 → 转成「选题简报」→ 生成角度。
            推荐是**下判断**，不是返回一个列表。
          </div>
        </div>
        <span className={`status ${sourceInfo ? "success" : "warning"}`}>
          {sourceInfo ? `已连热点源（${sourceInfo.count} 个）` : "热点源未连接"}
        </span>
      </div>

      <div className="panel-body">
        {sourcesError && (
          <div className="empty-state" data-od-id="hotspot-source-error">
            <p className="empty-title">没找到热点 MCP 连接。</p>
            <p className="empty-sub">
              热点走独立的 stdio MCP Server（P2）。先登记它，再回到本页：
            </p>
            <p className="mono" style={{ fontSize: 11 }}>
              stepwork-cli mcp add --command "python -m stepwork_hotspot_mcp.server"
            </p>
            {/* 这里曾有一句「登记命令里要含：{sourceInfo.marker}」，但它**永远不会渲染** ——
                本块由 `sourcesError` 进入，而设置 `sourcesError` 的那个 catch 顺手把
                `sourceInfo` 置成了 null（为了让下面那排按钮不出现）。条件恒假。
                删掉不亏：marker 是常量 `stepwork-hotspot-mcp`，而后端失败时的 hint
                （`hotspot/mcp.py` 的 NOT_FOUND 分支）**已经把「登记命令要含它」写在
                错误文本里**，那条会经 `describeCommandError` 原样显示在下面。 */}
            <p className="error-text">{sourcesError}</p>
          </div>
        )}

        {sourceInfo && (
          <>
            <div className="inline-actions">
              <button
                type="button"
                className="btn small primary"
                disabled={isBusy}
                onClick={() => void discover()}
                data-od-id="hotspot-discover"
              >
                抓取热点
              </button>
              <button
                type="button"
                className="btn small ghost"
                disabled={isBusy}
                onClick={() => void recommend()}
                data-od-id="hotspot-recommend"
              >
                推荐
              </button>
              {sourceInfo.marker && (
                <span className="panel-meta">源标记 {sourceInfo.marker}</span>
              )}
            </div>

            {summary && (
              <p className="panel-meta" data-od-id="hotspot-discover-summary">
                抓到 {summary.count} 条，落库 {summary.saved} 条，来自{" "}
                {summary.sources.length} 个源
                {summary.errors.length > 0 && (
                  <>
                    ；<strong>{summary.errors.length} 个源失败</strong>：
                    {summary.errors.map((e) => `${e.source}（${e.error}）`).join("、")}
                  </>
                )}
                {summary.skipped.length > 0 && (
                  <>
                    ；跳过 {summary.skipped.length} 个：
                    {summary.skipped.map((s) => `${s.source}（${s.reason}）`).join("、")}
                  </>
                )}
              </p>
            )}
          </>
        )}

        {error && (
          <p className="error-text" data-od-id="hotspot-error">
            {error}
          </p>
        )}

        {isBusy && <p className="feature-sub">处理中…</p>}

        {view && (
          <>
            <div className="panel-meta" data-od-id="hotspot-candidate-line">
              {candidateLine(view.count, view.considered)}
              <span className={`status ${view.reasonSource === "ai" ? "ai" : "warning"}`}>
                {reasonBadge(view.reasonSource).label}
              </span>
            </div>

            {notice && (
              <p className="error-text" data-od-id="hotspot-brand-notice">
                {notice}
              </p>
            )}

            {view.reasonNote && <p className="panel-meta">{view.reasonNote}</p>}

            {view.recommendations.length === 0 ? (
              <div className="empty-state" data-od-id="hotspot-empty">
                <p className="empty-title">本次没有可推荐的热点。</p>
                <p className="empty-sub">{emptyHint(view.considered)}</p>
              </div>
            ) : (
              <ul className="approval-list">
                {view.recommendations.map((item) => {
                  const badge = reasonBadge(item.reasonSource);
                  const fb = feedback[item.id];
                  return (
                    <li
                      key={item.id}
                      className="panel"
                      data-od-id={`hotspot-${item.id}`}
                    >
                      <div className="panel-head">
                        <div>
                          <h3 className="panel-title">{item.title}</h3>
                          <div className="panel-meta">
                            {item.source} · 总分 {item.score}
                          </div>
                        </div>
                        <span
                          className={`status ${
                            item.reasonSource === "ai" ? "ai" : "warning"
                          }`}
                          title={badge.hint}
                        >
                          {badge.label}
                        </span>
                      </div>

                      <div className="panel-body">
                        <p data-od-id={`hotspot-reason-${item.id}`}>{item.reason}</p>
                        <p className="panel-meta">{badge.hint}</p>

                        {item.risks.length > 0 && (
                          <p className="error-text">
                            风险：{item.risks.join("；")}
                          </p>
                        )}

                        <dl className="provenance">
                          <div className="provenance-row">
                            <dt>打分口径</dt>
                            <dd>{SCORE_FORMULA}</dd>
                          </div>
                          {breakdownRows(item.breakdown).map((row) => (
                            <div className="provenance-row" key={row.key}>
                              <dt>{row.label}</dt>
                              <dd>
                                {row.percent}%
                                {row.kind === "gate" && "（乘性闸门，不是加权项）"}
                                {row.kind === "penalty" && "（乘性扣减）"}
                              </dd>
                            </div>
                          ))}
                        </dl>

                        <div className="inline-actions">
                          <button
                            type="button"
                            className="btn small primary"
                            disabled={busyId === item.id}
                            onClick={() => void convert(item)}
                            data-od-id={`hotspot-convert-${item.id}`}
                          >
                            转成选题简报
                          </button>
                          <button
                            type="button"
                            className="btn small ghost"
                            disabled={busyId === item.id}
                            onClick={() => void sendFeedback(item.id, "adopted")}
                          >
                            感兴趣
                          </button>
                          <button
                            type="button"
                            className="btn small ghost"
                            disabled={busyId === item.id}
                            onClick={() => void sendFeedback(item.id, "ignored")}
                          >
                            不感兴趣
                          </button>
                          {fb && <span className="panel-meta">已记录：{fb}</span>}
                        </div>

                        {item.url && (
                          <p className="panel-meta mono" style={{ fontSize: 11 }}>
                            {item.url}
                          </p>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </>
        )}

        {brief && (
          <div className="panel" data-od-id="hotspot-brief">
            <div className="panel-head">
              <div>
                <h3 className="panel-title">选题简报 · {brief.title}</h3>
                <div className="panel-meta">
                  {brief.source} · {brief.hotspotId}
                </div>
              </div>
              <span className="status warning">
                {trustBadge(brief.trustLevel) ?? "内部素材"}
              </span>
            </div>
            <div className="panel-body">
              <p className="panel-meta">
                复核状态 {brief.reviewState} · 理由来源 {brief.reasonSource} ·
                打分分解{brief.breakdownAttached ? "已附带" : "未附带"}
              </p>
              {reusedNotice(brief.reused) && (
                <p className="panel-meta">{reusedNotice(brief.reused)}</p>
              )}

              {/* 简报正文自带免责头（「可见的诚实」），原样渲染不再二次加工 */}
              <pre
                className="mono"
                style={{ fontSize: 11, whiteSpace: "pre-wrap" }}
                data-od-id="hotspot-brief-body"
              >
                {brief.brief}
              </pre>

              {nextHint && <p className="panel-meta">{nextHint}</p>}

              <div className="inline-actions">
                <button
                  type="button"
                  className="btn small primary"
                  onClick={handOffToAngles}
                  data-od-id="hotspot-to-angles"
                >
                  用这份简报生成角度
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
