/**
 * 工作台首页 — 真实接入后端
 *
 * 接通命令：
 *   - ListProjects：填充最近项目列表 + 进行中项目数 metric
 *   - 各 store：转写/渲染任务状态聚合为 metric
 *
 * metrics 全部基于真实 store/后端数据，不再硬编码。
 */

import { useEffect, useState } from "react";
import { buildEnvelope, dispatchCommand } from "@/lib/tauri";
import { useViewStore } from "@/stores/useViewStore";
import { useTranscriptStore } from "@/stores/useTranscriptStore";
import { useRenderStore } from "@/stores/useRenderStore";
import { useImportStore } from "@/stores/useImportStore";

interface ProjectRow {
  id: string;
  title: string;
  status: string;
  updated_at: string;
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

function formatMetric(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

function formatRelative(iso: string): string {
  try {
    const d = new Date(iso);
    const now = Date.now();
    const diff = Math.floor((now - d.getTime()) / 1000);
    if (diff < 60) return "刚刚";
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
    return `${pad2(d.getMonth() + 1)}/${pad2(d.getDate())}`;
  } catch {
    return iso;
  }
}

export function WorkbenchView() {
  const setView = useViewStore((s) => s.setView);
  const setCreateSubView = useViewStore((s) => s.setCreateSubView);
  const setSelectedProjectId = useViewStore((s) => s.setSelectedProjectId);

  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [loadingProjects, setLoadingProjects] = useState(true);

  // store 状态作为 metric 数据源
  const importAssets = useImportStore((s) => s.assets);
  const transcriptJobs = useTranscriptStore((s) => s.jobs);
  const renderStatus = useRenderStore((s) => s.status);
  const renderProgress = useRenderStore((s) => s.progress);

  useEffect(() => {
    void (async () => {
      try {
        const env = buildEnvelope("ListProjects", "ws-local", null, {});
        const res = await dispatchCommand(env);
        if (!res.ok) return;
        const detail = (res.detail ?? {}) as { projects?: ProjectRow[] };
        setProjects(detail.projects ?? []);
      } catch {
        /* 静默 */
      } finally {
        setLoadingProjects(false);
      }
    })();
  }, []);

  const goCreate = (sub: "import" | "analysis" | "angle" | "script" | "render") => {
    setCreateSubView(sub);
    setView("create");
  };

  // 真实 metric 计算
  const activeProjectCount = projects.length;
  const runningTaskCount =
    transcriptJobs.filter((j) => j.status === "running" || j.status === "pending").length +
    (renderStatus === "running" ? 1 : 0);
  const pendingConfirmCount =
    transcriptJobs.filter((j) => j.status === "succeeded").length +
    (renderStatus === "succeeded" ? 1 : 0);
  const failedTaskCount =
    transcriptJobs.filter((j) => j.status === "failed").length +
    (renderStatus === "failed" ? 1 : 0);

  function refreshAll() {
    setLoadingProjects(true);
    void (async () => {
      try {
        const env = buildEnvelope("ListProjects", "ws-local", null, {});
        const res = await dispatchCommand(env);
        if (!res.ok) return;
        const detail = (res.detail ?? {}) as { projects?: ProjectRow[] };
        setProjects(detail.projects ?? []);
      } finally {
        setLoadingProjects(false);
      }
    })();
  }

  return (
    <>
      <section className="page-head" data-od-id="workbench-heading">
        <div>
          <p className="eyebrow">PROJECT COMMAND · V0.1</p>
          <h1 data-od-id="workbench-title">继续完成你的内容草稿</h1>
          <p className="page-subtitle">
            从素材导入到视频导出，每一步都有来源、版本与任务记录。
            {loadingProjects
              ? " 正在加载工作区数据…"
              : ` 当前 ${activeProjectCount} 个项目，已导入 ${importAssets.length} 个素材。`}
          </p>
        </div>
        <button
          className="btn"
          type="button"
          data-od-id="refresh-workbench"
          onClick={refreshAll}
          disabled={loadingProjects}
        >
          {loadingProjects ? "刷新中…" : "刷新状态"}
        </button>
      </section>

      <section className="flow-strip flow-five" aria-label="MVP 创作流程" data-od-id="core-flow">
        <a className="flow-step active" onClick={() => goCreate("import")} data-od-id="flow-import">
          <div className="flow-no">01 · IMPORT</div>
          <div className="flow-name">导入素材</div>
        </a>
        <a className="flow-step" onClick={() => goCreate("analysis")} data-od-id="flow-analyze">
          <div className="flow-no">02 · ANALYZE</div>
          <div className="flow-name">转写分析</div>
        </a>
        <a className="flow-step" onClick={() => goCreate("angle")} data-od-id="flow-angle">
          <div className="flow-no">03 · ANGLE</div>
          <div className="flow-name">原创角度</div>
        </a>
        <a className="flow-step" onClick={() => goCreate("script")} data-od-id="flow-script">
          <div className="flow-no">04 · SCRIPT</div>
          <div className="flow-name">脚本创作</div>
        </a>
        <a className="flow-step" onClick={() => goCreate("render")} data-od-id="flow-render">
          <div className="flow-no">05 · RENDER</div>
          <div className="flow-name">视频草稿</div>
        </a>
      </section>

      <section className="grid grid-4" data-od-id="workbench-metrics">
        <article className="metric" data-od-id="metric-active-projects">
          <div className="metric-label">进行中项目</div>
          <div className="metric-value num">{formatMetric(activeProjectCount)}</div>
          <div className="metric-delta">{loadingProjects ? "加载中" : "来自工作区"}</div>
        </article>
        <article className="metric" data-od-id="metric-active-tasks">
          <div className="metric-label">运行任务</div>
          <div className="metric-value num">{formatMetric(runningTaskCount)}</div>
          <div className="metric-delta">
            {runningTaskCount > 0 ? "转写 · 渲染" : "暂无运行任务"}
          </div>
        </article>
        <article className="metric" data-od-id="metric-review">
          <div className="metric-label">已完成可继续</div>
          <div className="metric-value num">{formatMetric(pendingConfirmCount)}</div>
          <div className="metric-delta">转写 · 渲染产物</div>
        </article>
        <article className={`metric${failedTaskCount > 0 ? " metric-danger" : ""}`} data-od-id="metric-failed">
          <div className="metric-label">需要处理</div>
          <div className="metric-value num">{formatMetric(failedTaskCount)}</div>
          <div className="metric-delta">
            {failedTaskCount > 0 ? "可重试" : "无异常"}
          </div>
        </article>
      </section>

      <section className="layout-main section-gap" data-od-id="workbench-main-grid">
        <article className="panel" data-od-id="recent-projects-panel">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">最近项目</h2>
              <div className="panel-meta">按最近更新时间排序</div>
            </div>
            <button
              className="btn small ghost"
              onClick={() => setView("projects")}
              data-od-id="view-all-projects"
            >
              查看全部
            </button>
          </div>
          <div className="data-list project-overview">
            {loadingProjects && (
              <p className="panel-meta" style={{ padding: 16 }}>
                正在加载项目列表…
              </p>
            )}
            {!loadingProjects && projects.length === 0 && (
              <p className="panel-meta" style={{ padding: 16 }}>
                尚无项目，点击下方「新建项目」开始。
              </p>
            )}
            {projects.slice(0, 5).map((p) => (
              <a
                key={p.id}
                className="data-row simple-row"
                onClick={() => {
                  setSelectedProjectId(p.id);
                  goCreate("import");
                }}
                data-od-id={`project-${p.id}`}
                style={{ cursor: "pointer" }}
              >
                <div>
                  <div className="row-title">{p.title}</div>
                  <div className="row-sub mono">{p.id.slice(0, 16)}…</div>
                </div>
                <span className="status">{p.status}</span>
                <span className="mono row-date">{formatRelative(p.updated_at)}</span>
              </a>
            ))}
          </div>
        </article>

        <aside className="grid" data-od-id="workbench-side-column">
          <article className="panel" data-od-id="recent-assets-panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">本次会话素材</h2>
                <div className="panel-meta">来自导入 store</div>
              </div>
              <span className="status">{importAssets.length}</span>
            </div>
            <div className="compact-list">
              {importAssets.length === 0 ? (
                <p className="panel-meta" style={{ padding: 12 }}>
                  尚无素材。点击「导入素材」开始。
                </p>
              ) : (
                importAssets.slice(0, 5).map((a) => (
                  <a key={a.id} onClick={() => goCreate("analysis")} style={{ cursor: "pointer" }}>
                    <strong>{a.local_uri.split(/[\\/]/).pop() ?? a.local_uri}</strong>
                    <span>{a.kind}</span>
                  </a>
                ))
              )}
            </div>
          </article>

          {renderStatus !== "idle" && (
            <article className="panel" data-od-id="render-status-panel">
              <div className="panel-head">
                <div>
                  <h2 className="panel-title">渲染状态</h2>
                  <div className="panel-meta">实时来自 render store</div>
                </div>
                <span
                  className={`status ${
                    renderStatus === "succeeded"
                      ? "success"
                      : renderStatus === "failed"
                        ? "danger"
                        : renderStatus === "running"
                          ? "ai"
                          : "warning"
                  }`}
                >
                  {renderStatus}
                </span>
              </div>
              <div className="panel-body">
                {renderStatus === "running" && (
                  <div
                    className="progress"
                    style={{ ["--progress" as string]: `${Math.round(renderProgress * 100)}%` }}
                  >
                    <span />
                  </div>
                )}
              </div>
            </article>
          )}
        </aside>
      </section>

      <div className="inline-actions" style={{ marginTop: 18 }}>
        <button
          type="button"
          className="btn primary"
          onClick={() => goCreate("import")}
          data-od-id="start-create"
        >
          开始创作
        </button>
      </div>
    </>
  );
}
