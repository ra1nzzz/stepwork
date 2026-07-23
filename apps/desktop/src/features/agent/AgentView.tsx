/**
 * Agent 互操作视图（W8）
 * 列出 Agent 任务（ListAgentTasks）与产物（ListAgentArtifacts）。
 * Agent 互操作将在 V0.2 启用；当前为占位数据模型，列表为空时显示空态。
 */

import { useEffect, useState } from "react";
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "@/lib/tauri";
import type { CommandResult } from "@/lib/types";

interface AgentTask {
  id: string;
  status: string;
  kind: string;
  created_at: string;
}

interface AgentArtifact {
  id: string;
  kind: string;
  produced_at: string;
}

export function AgentView() {
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [artifacts, setArtifacts] = useState<AgentArtifact[]>([]);
  const [isBusy, setIsBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setIsBusy(true);
      setError(null);
      try {
        const taskEnv = buildEnvelope("ListAgentTasks", getWorkspaceId(), null, {});
        const taskRes: CommandResult = await dispatchCommand(taskEnv);
        if (cancelled) return;
        if (taskRes.ok) {
          const d = (taskRes.detail ?? {}) as { tasks?: AgentTask[] };
          setTasks(d.tasks ?? []);
        } else {
          setError(taskRes.error ?? "加载任务失败");
        }

        const artEnv = buildEnvelope("ListAgentArtifacts", getWorkspaceId(), null, {});
        const artRes: CommandResult = await dispatchCommand(artEnv);
        if (cancelled) return;
        if (artRes.ok) {
          const d = (artRes.detail ?? {}) as { artifacts?: AgentArtifact[] };
          setArtifacts(d.artifacts ?? []);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setIsBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const isEmpty = !isBusy && tasks.length === 0 && artifacts.length === 0 && !error;

  return (
    <section className="feature-view" data-od-id="agent-view">
      <header className="feature-head">
        <h1>Agent</h1>
        <p className="feature-sub">Agent 互操作面板：查看 Agent 任务与产物</p>
      </header>

      {isBusy && <p className="feature-sub">加载中…</p>}

      {error && (
        <p className="error-text" data-od-id="agent-error">
          {error}
        </p>
      )}

      {isEmpty && (
        <div className="empty-state" data-od-id="agent-empty">
          <p className="empty-title">Agent 互操作将在 V0.2 启用。</p>
          <p className="empty-sub">当前可查看占位数据模型。</p>
        </div>
      )}

      {tasks.length > 0 && (
        <div className="agent-section">
          <h2>任务</h2>
          <ul className="agent-list">
            {tasks.map((t) => (
              <li key={t.id} className="agent-item">
                <span className="status-badge" data-status={t.status}>
                  {t.status}
                </span>
                <span className="agent-kind">{t.kind}</span>
                <span className="agent-meta">{t.id.slice(0, 8)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {artifacts.length > 0 && (
        <div className="agent-section">
          <h2>产物</h2>
          <ul className="agent-list">
            {artifacts.map((a) => (
              <li key={a.id} className="agent-item">
                <span className="agent-kind">{a.kind}</span>
                <span className="agent-meta">{a.id.slice(0, 8)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
