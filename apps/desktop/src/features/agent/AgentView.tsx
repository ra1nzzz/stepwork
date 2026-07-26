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

/**
 * 信任等级中文名（PRD §10.4「分析和外部 Agent 结果显示来源及信任等级」）。
 * 取值见 schemas/artifact-envelope.schema.json。
 */
const TRUST_LABELS: Record<string, string> = {
  "trusted-local": "本地可信",
  "trusted-remote": "远端可信",
  "verified-external": "外部已验证",
  "external-unverified": "外部未验证",
  "generated-unverified": "生成未验证",
  "human-reviewed": "人工已复核",
};

/** 未复核的外部产物用警示色，提醒用户先核对再采用 */
function trustClass(level: string | null | undefined): string {
  if (!level) return "warning";
  if (level === "human-reviewed" || level.startsWith("trusted")) return "success";
  if (level === "verified-external") return "ai";
  return "warning";
}

/** PRD-AGT-007：Agent 协议连接 */
interface AgentConnection {
  id: string;
  protocol: string;
  endpoint_or_command: string;
  local_or_remote: string;
  trust_level: string;
  status: string;
  task_count: number;
  created_at: string;
}

interface AgentArtifact {
  id: string;
  /** 信任等级（PRD-AGT-003 / §10.4） */
  trust_level?: string | null;
  review_state?: string | null;
  producer_agent_id?: string | null;
  artifact_type?: string | null;
  kind: string;
  produced_at: string;
}

export function AgentView() {
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [artifacts, setArtifacts] = useState<AgentArtifact[]>([]);
  // PRD-AGT-007：连接列表（可启停 / 删除）
  const [connections, setConnections] = useState<AgentConnection[]>([]);
  const [connBusy, setConnBusy] = useState<string | null>(null);

  async function loadConnections() {
    const env = buildEnvelope("ListAgentConnections", getWorkspaceId(), null, {});
    const res = await dispatchCommand(env);
    if (res.ok) {
      const d = (res.detail ?? {}) as { connections?: AgentConnection[] };
      setConnections(d.connections ?? []);
    }
  }

  async function setConnStatus(id: string, status: "active" | "inactive") {
    setConnBusy(id);
    try {
      const env = buildEnvelope(
        "SetAgentConnectionStatus",
        getWorkspaceId(),
        null,
        { connectionId: id, status },
      );
      await dispatchCommand(env);
      await loadConnections();
    } finally {
      setConnBusy(null);
    }
  }

  async function deleteConn(id: string) {
    setConnBusy(id);
    try {
      const env = buildEnvelope("DeleteAgentConnection", getWorkspaceId(), null, {
        connectionId: id,
      });
      await dispatchCommand(env);
      await loadConnections();
    } finally {
      setConnBusy(null);
    }
  }
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

        void loadConnections();
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

      {/* PRD-AGT-007：连接管理（启停 / 删除；停用后该通道调用会被拒） */}
      <div className="agent-section">
        <h2>协议连接</h2>
        {connections.length === 0 ? (
          <p className="panel-meta">
            尚无连接记录。外部 Agent（MCP / A2A / ACP）首次调用后会出现在这里。
          </p>
        ) : (
          <ul className="agent-list">
            {connections.map((c) => (
              <li key={c.id} className="agent-item">
                <span className="agent-kind">{c.protocol}</span>
                <span className={`status ${c.status === "active" ? "success" : "warning"}`}>
                  {c.status === "active" ? "已启用" : "已停用"}
                </span>
                <span className={`status ${trustClass(c.trust_level)}`}>
                  {TRUST_LABELS[c.trust_level] ?? c.trust_level}
                </span>
                <span className="agent-meta">
                  {c.local_or_remote} · {c.task_count} 个任务
                </span>
                <span className="inline-actions">
                  <button
                    type="button"
                    className="btn small ghost"
                    disabled={connBusy === c.id}
                    onClick={() =>
                      void setConnStatus(
                        c.id,
                        c.status === "active" ? "inactive" : "active",
                      )
                    }
                  >
                    {c.status === "active" ? "停用" : "启用"}
                  </button>
                  <button
                    type="button"
                    className="btn small ghost"
                    disabled={connBusy === c.id}
                    onClick={() => void deleteConn(c.id)}
                  >
                    删除
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {artifacts.length > 0 && (
        <div className="agent-section">
          <h2>产物</h2>
          <ul className="agent-list">
            {artifacts.map((a) => (
              <li key={a.id} className="agent-item">
                <span className="agent-kind">{a.artifact_type ?? a.kind}</span>
                {/* PRD §10.4：外部产物必须显示来源与信任等级 */}
                <span className={`status ${trustClass(a.trust_level)}`}>
                  {TRUST_LABELS[a.trust_level ?? ""] ?? a.trust_level ?? "未知信任等级"}
                </span>
                {a.review_state && (
                  <span className="agent-meta">
                    {a.review_state === "pending_review" ? "待复核" : a.review_state}
                  </span>
                )}
                {a.producer_agent_id && (
                  <span className="agent-meta">来源 {a.producer_agent_id}</span>
                )}
                <span className="agent-meta">{a.id.slice(0, 8)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
