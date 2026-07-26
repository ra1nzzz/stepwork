/**
 * 任务中心页 — 真实接入后端（Tranche 1 / T4 + T5）
 *
 * 二级页签：任务 / Agent 活动 / 溯源
 *   - 任务：持久化任务列表（ListJobs，每 ~2s 轮询，含应用重启前的任务）为事实源；
 *     渲染任务的会话内实时状态与诊断包保留在侧栏
 *   - Agent 活动：features/agent/AgentView（ListAgentTasks / ListAgentArtifacts）
 *   - 溯源：features/provenance/ProvenanceView（GetProvenance）
 *
 * 实时性：worker-notification 的 job.progress 通知会经 useJobEventsStore
 * 直接更新列表行，轮询作为兜底（见 lib/workerEvents.ts）。
 */

import { useEffect, useState } from "react";
import { buildEnvelope, dispatchCommand } from "@/lib/tauri";
import { useJobEventsStore } from "@/stores/useJobEventsStore";
import { useRenderStore, type RenderStatus } from "@/stores/useRenderStore";
import { AgentView } from "@/features/agent/AgentView";
import { ProvenanceView } from "@/features/provenance/ProvenanceView";
import type { JobState, PersistedJob } from "@/lib/types";

type TaskTab = "jobs" | "agent" | "provenance";

const TABS: { id: TaskTab; label: string }[] = [
  { id: "jobs", label: "任务" },
  { id: "agent", label: "Agent 活动" },
  { id: "provenance", label: "溯源" },
];

/** ListJobs 轮询间隔（ms）；job.progress 事件为主，轮询兜底 */
const POLL_INTERVAL_MS = 2000;

interface DiagnosticsResult {
  bundle_path: string;
  size_bytes: number;
  desensitized: boolean;
}

function jobStateLabel(s: JobState): string {
  switch (s) {
    case "pending": return "等待中";
    case "leased": return "已领取";
    case "running": return "运行中";
    case "succeeded": return "完成";
    case "failed": return "失败";
    case "cancelled": return "已取消";
    case "cancelled-requested": return "取消中";
    case "expired": return "已过期";
    default: return s;
  }
}

function jobStateClass(s: JobState): string {
  switch (s) {
    case "pending":
    case "leased":
    case "running": return "ai";
    case "succeeded": return "success";
    case "failed": return "danger";
    default: return "warning";
  }
}

function renderStatusLabel(s: RenderStatus): string {
  switch (s) {
    case "idle": return "未开始";
    case "running": return "渲染中";
    case "succeeded": return "完成";
    case "failed": return "失败";
    case "cancelled": return "已取消";
    default: return s;
  }
}

function renderStatusClass(s: RenderStatus): string {
  switch (s) {
    case "running": return "ai";
    case "succeeded": return "success";
    case "failed": return "danger";
    case "cancelled": return "warning";
    default: return "warning";
  }
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function JobRow({ job }: { job: PersistedJob }) {
  const active =
    job.state === "running" || job.state === "leased" || job.state === "pending";
  return (
    <div
      className={`task-item${job.state === "failed" ? " task-error" : ""}`}
      data-state={job.state}
      data-od-id={`job-${job.id}`}
    >
      <div className="task-top">
        <div>
          <div className="task-name">
            {job.job_type}
            <span className="mono row-sub" style={{ marginLeft: 8 }}>
              {job.id.slice(0, 8)}
            </span>
          </div>
          <div className="row-sub">
            {job.stage ? `阶段 ${job.stage}` : "无阶段"}
            {` · 更新 ${formatTime(job.updated_at)}`}
          </div>
        </div>
        <span className={`status ${jobStateClass(job.state)}`}>
          {jobStateLabel(job.state)}
          {active ? ` ${Math.round(job.progress * 100)}%` : ""}
        </span>
      </div>

      {active && (
        <div
          className="progress"
          style={{ ["--progress" as string]: `${Math.round(job.progress * 100)}%` }}
        >
          <span />
        </div>
      )}

      {job.error_code && (
        <p className="panel-meta section-gap" style={{ color: "var(--danger)" }}>
          {job.error_code}
        </p>
      )}
    </div>
  );
}

export function TasksView() {
  const [tab, setTab] = useState<TaskTab>("jobs");

  const jobs = useJobEventsStore((s) => s.jobs);
  const jobsError = useJobEventsStore((s) => s.error);
  const lastRefreshAt = useJobEventsStore((s) => s.lastRefreshAt);

  const renderStatus = useRenderStore((s) => s.status);
  const renderJobId = useRenderStore((s) => s.jobId);
  const renderProgress = useRenderStore((s) => s.progress);
  const renderError = useRenderStore((s) => s.error);

  const [diagnostics, setDiagnostics] = useState<DiagnosticsResult | null>(null);
  const [busyDiag, setBusyDiag] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 持久化任务列表轮询：挂载即拉取 + 每 ~2s 刷新（事实源，覆盖重启前任务）
  useEffect(() => {
    const refresh = () => void useJobEventsStore.getState().refresh();
    refresh();
    const timer = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, []);

  async function handleDiagnostics() {
    setBusyDiag(true);
    setError(null);
    try {
      const env = buildEnvelope("ExportDiagnosticsBundle", "ws-local", null, {
        desensitize: true,
      });
      const res = await dispatchCommand(env);
      if (!res.ok) throw new Error(res.error ?? "DIAGNOSTICS_FAILED");
      const detail = (res.detail ?? {}) as Partial<DiagnosticsResult>;
      setDiagnostics({
        bundle_path: detail.bundle_path ?? "",
        size_bytes: detail.size_bytes ?? 0,
        desensitized: detail.desensitized ?? false,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyDiag(false);
    }
  }

  const failedCount = jobs.filter((j) => j.state === "failed").length;
  const runningCount = jobs.filter(
    (j) => j.state === "running" || j.state === "leased" || j.state === "pending",
  ).length;

  return (
    <>
      <section className="page-head" data-od-id="tasks-heading">
        <div>
          <p className="eyebrow">TASKS &amp; ARTIFACTS</p>
          <h1>任务状态与 Agent 产物</h1>
          <p className="page-subtitle">
            任务列表来自后端持久化队列（含重启前任务），实时进度经 worker 通知推送。
          </p>
        </div>
        <span className={`status ${failedCount > 0 ? "danger" : runningCount > 0 ? "ai" : "success"}`}>
          {failedCount > 0
            ? `${failedCount} 个异常`
            : runningCount > 0
              ? `${runningCount} 个运行`
              : "全部完成"}
        </span>
      </section>

      <nav className="subnav" aria-label="任务中心页签" data-od-id="tasks-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={tab === t.id ? "active" : ""}
            onClick={() => setTab(t.id)}
            data-od-id={`tasks-tab-${t.id}`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "agent" && <AgentView />}
      {tab === "provenance" && <ProvenanceView />}

      {tab === "jobs" && (
        <section className="layout-main">
          <article className="panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">持久化任务</h2>
                <div className="panel-meta">
                  {jobs.length} 个任务 · 来自 content_jobs
                  {lastRefreshAt ? ` · 刷新于 ${formatTime(lastRefreshAt)}` : ""}
                </div>
              </div>
            </div>

            <div className="panel-body task-list">
              {jobsError && (
                <p className="panel-meta" style={{ color: "var(--danger)", margin: 0 }}>
                  {jobsError}
                </p>
              )}
              {!jobsError && jobs.length === 0 ? (
                <p className="panel-meta" style={{ margin: 0 }}>
                  {lastRefreshAt
                    ? "尚无任务。进入「创作」开始转写或渲染。"
                    : "正在加载任务列表…"}
                </p>
              ) : (
                jobs.map((job) => <JobRow key={job.id} job={job} />)
              )}
            </div>
          </article>

          <aside className="grid">
            <article className="panel">
              <div className="panel-head">
                <div>
                  <h2 className="panel-title">渲染任务（本次会话）</h2>
                  <div className="panel-meta">
                    {renderJobId ? `job ${renderJobId.slice(0, 12)}…` : "无任务"}
                  </div>
                </div>
                <span className={`status ${renderStatusClass(renderStatus)}`}>
                  {renderStatusLabel(renderStatus)}
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
                {renderError && (
                  <p className="panel-meta section-gap" style={{ color: "var(--danger)" }}>
                    {renderError}
                  </p>
                )}
                {renderStatus === "idle" && (
                  <p className="panel-meta" style={{ margin: 0 }}>
                    尚无渲染任务。进入「创作 · 视频草稿」开始。
                  </p>
                )}
              </div>
            </article>

            <article className="panel">
              <div className="panel-head">
                <div>
                  <h2 className="panel-title">诊断包</h2>
                  <div className="panel-meta">导出后端日志与配置</div>
                </div>
              </div>
              <div className="panel-body">
                <button
                  className="btn small"
                  type="button"
                  id="downloadDiagnostics"
                  onClick={() => void handleDiagnostics()}
                  disabled={busyDiag}
                >
                  {busyDiag ? "导出中…" : "下载诊断包"}
                </button>
                {diagnostics && (
                  <div style={{ marginTop: 12 }}>
                    <p className="panel-meta" style={{ margin: 0 }}>
                      路径：{diagnostics.bundle_path}
                    </p>
                    <p className="panel-meta" style={{ margin: 0 }}>
                      大小：{(diagnostics.size_bytes / 1024).toFixed(1)} KB ·{" "}
                      {diagnostics.desensitized ? "已脱敏" : "未脱敏"}
                    </p>
                  </div>
                )}
                {error && (
                  <p className="error-text" style={{ color: "var(--danger)", marginTop: 8 }}>
                    {error}
                  </p>
                )}
              </div>
            </article>
          </aside>
        </section>
      )}
    </>
  );
}
