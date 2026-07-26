/**
 * 任务中心页 — 真实接入后端
 *
 * 接通命令：
 *   - ExportDiagnosticsBundle：下载诊断包按钮
 *   - ListAgentArtifacts：列出 Agent 待确认产物（V0.2 启用，当前表为空）
 *
 * 由于后端无 ListJobs 命令，任务列表聚合自各 store 的本地 job 状态：
 *   - useTranscriptStore.jobs：转写任务（本地状态，反映真实 dispatch 结果）
 *   - useRenderStore：渲染任务（含真实 jobId）
 * GetJobStatus 命令后端已实现，但当前未在前端轮询（任务状态由各 store 在
 * dispatch 返回时同步更新）；如需离开页面后保持状态，可后续加轮询。
 */

import { useEffect, useState } from "react";
import { buildEnvelope, dispatchCommand } from "@/lib/tauri";
import { useTranscriptStore, type TranscriptJob } from "@/stores/useTranscriptStore";
import { useRenderStore, type RenderStatus } from "@/stores/useRenderStore";

interface AgentArtifact {
  id: string;
  title?: string;
  state?: string;
  summary?: string;
}

interface DiagnosticsResult {
  bundle_path: string;
  size_bytes: number;
  desensitized: boolean;
}

function transcribeStatusLabel(s: TranscriptJob["status"]): string {
  switch (s) {
    case "pending": return "等待中";
    case "running": return "转写中";
    case "succeeded": return "完成";
    case "failed": return "失败";
    case "cancelled": return "已取消";
    default: return s;
  }
}

function transcribeStatusClass(s: TranscriptJob["status"]): string {
  switch (s) {
    case "running":
    case "pending": return "ai";
    case "succeeded": return "success";
    case "failed": return "danger";
    case "cancelled": return "warning";
    default: return "";
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

export function TasksView() {
  const transcriptJobs = useTranscriptStore((s) => s.jobs);
  const renderStatus = useRenderStore((s) => s.status);
  const renderJobId = useRenderStore((s) => s.jobId);
  const renderProgress = useRenderStore((s) => s.progress);
  const renderError = useRenderStore((s) => s.error);

  const [agentArtifacts, setAgentArtifacts] = useState<AgentArtifact[]>([]);
  const [diagnostics, setDiagnostics] = useState<DiagnosticsResult | null>(null);
  const [busyDiag, setBusyDiag] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 加载 Agent 产物
  useEffect(() => {
    void (async () => {
      try {
        const env = buildEnvelope("ListAgentArtifacts", "ws-local", null, {});
        const res = await dispatchCommand(env);
        if (!res.ok) return; // 静默失败，Agent 表 V0.2 启用
        const detail = (res.detail ?? {}) as { artifacts?: AgentArtifact[] };
        setAgentArtifacts(detail.artifacts ?? []);
      } catch {
        /* Agent 互操作 V0.2 启用 */
      }
    })();
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

  const failedCount =
    transcriptJobs.filter((j) => j.status === "failed").length +
    (renderStatus === "failed" ? 1 : 0);
  const runningCount =
    transcriptJobs.filter((j) => j.status === "running" || j.status === "pending").length +
    (renderStatus === "running" ? 1 : 0);

  return (
    <>
      <section className="page-head" data-od-id="tasks-heading">
        <div>
          <p className="eyebrow">TASKS &amp; ARTIFACTS</p>
          <h1>任务状态与 Agent 产物</h1>
          <p className="page-subtitle">
            转写与渲染任务实时反映本地 store 状态；Agent 互操作在 V0.2 启用。
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

      <section className="layout-main">
        <article className="panel">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">转写任务</h2>
              <div className="panel-meta">
                {transcriptJobs.length} 个任务 · 来自创作流程
              </div>
            </div>
          </div>

          <div className="panel-body task-list">
            {transcriptJobs.length === 0 ? (
              <p className="panel-meta" style={{ margin: 0 }}>
                尚无转写任务。进入「创作 · 素材分析」开始。
              </p>
            ) : (
              transcriptJobs.map((job) => (
                <div
                  key={job.id}
                  className={`task-item${job.status === "failed" ? " task-error" : ""}`}
                  data-state={job.status}
                >
                  <div className="task-top">
                    <div>
                      <div className="task-name">
                        {job.assetId ? `转写 ${job.assetId.slice(0, 8)}` : "外部素材转写"}
                      </div>
                      <div className="row-sub">
                        {job.versionId ? `版本 ${job.versionId.slice(0, 8)}` : "无版本"}
                        {job.language ? ` · ${job.language}` : ""}
                      </div>
                    </div>
                    <span className={`status ${transcribeStatusClass(job.status)}`}>
                      {transcribeStatusLabel(job.status)}
                    </span>
                  </div>

                  {job.error && (
                    <p className="panel-meta section-gap" style={{ color: "var(--danger)" }}>
                      {job.error}
                    </p>
                  )}
                </div>
              ))
            )}
          </div>
        </article>

        <aside className="grid">
          <article className="panel">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">渲染任务</h2>
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
                <h2 className="panel-title">Agent 待确认产物</h2>
                <div className="panel-meta">V0.2 启用</div>
              </div>
              <span className="status warning">{agentArtifacts.length} 个</span>
            </div>
            <div className="panel-body">
              {agentArtifacts.length === 0 ? (
                <p style={{ margin: 0, color: "var(--muted)" }}>
                  Agent 互操作在 V0.2 启用，当前无待确认产物。
                </p>
              ) : (
                agentArtifacts.map((a) => (
                  <div key={a.id}>
                    <h3>{a.title ?? a.id}</h3>
                    <p style={{ color: "var(--muted)" }}>{a.summary ?? ""}</p>
                  </div>
                ))
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
    </>
  );
}
