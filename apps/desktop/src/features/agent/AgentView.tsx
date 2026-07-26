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

/**
 * 出站 MCP 连接的 protocol 值（PRD-AGT-004）。与入站 ``mcp`` 分开：
 * 前者是「我们去连别人」，后者是「别人调我们」，能做的操作不同。
 */
const MCP_CLIENT_PROTOCOL = "mcp-client";

/** 外部 MCP Server 暴露的一个工具 */
interface McpTool {
  name: string;
  description: string;
}

/**
 * 该连接的工具名摘要。优先用刚刷新到的内存结果，否则解析后端存的
 * ``capabilities`` JSON —— 后者可能是坏数据（外部 Server 写的），
 * 解析失败时降级成提示文案而不是让整个列表崩掉。
 */
function mcpToolNames(conn: AgentConnection, fresh?: McpTool[]): string {
  let tools = fresh;
  if (!tools) {
    try {
      const parsed: unknown = JSON.parse(conn.capabilities || "[]");
      tools = Array.isArray(parsed) ? (parsed as McpTool[]) : [];
    } catch {
      return "工具目录不可读";
    }
  }
  if (tools.length === 0) return "无工具";
  return `工具：${tools.map((t) => t.name).join("、")}`;
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
  /** 出站连接的工具目录（JSON 字符串，后端写入） */
  capabilities?: string | null;
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
  // PRD-AGT-004：出站 MCP 客户端
  const [mcpCommand, setMcpCommand] = useState("");
  const [mcpBusy, setMcpBusy] = useState(false);
  const [mcpError, setMcpError] = useState<string | null>(null);
  const [mcpTools, setMcpTools] = useState<Record<string, McpTool[]>>({});

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

  /* PRD-AGT-004：出站 MCP —— 连外部搜索/知识库 Server */

  async function addMcpServer() {
    const command = mcpCommand.trim();
    if (!command) return;
    setMcpBusy(true);
    setMcpError(null);
    try {
      const env = buildEnvelope("AddMcpServer", getWorkspaceId(), null, { command });
      const res = await dispatchCommand(env);
      if (!res.ok) {
        // 后端「登记即探测」：连不上就不落库，这里如实回显失败原因
        setMcpError(res.error ?? "连接失败");
        return;
      }
      setMcpCommand("");
      await loadConnections();
    } finally {
      setMcpBusy(false);
    }
  }

  /** 刷新某条出站连接的工具目录（对方升级后用） */
  async function refreshTools(id: string) {
    setConnBusy(id);
    setMcpError(null);
    try {
      const env = buildEnvelope("ListMcpTools", getWorkspaceId(), null, {
        connectionId: id,
      });
      const res = await dispatchCommand(env);
      if (!res.ok) {
        setMcpError(res.error ?? "刷新工具目录失败");
        return;
      }
      const d = (res.detail ?? {}) as { tools?: McpTool[] };
      setMcpTools((prev) => ({ ...prev, [id]: d.tools ?? [] }));
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

      {/* PRD-AGT-004：接入外部 MCP Server（出站方向） */}
      <div className="agent-section" data-od-id="mcp-client-add">
        <h2>连接外部 MCP Server</h2>
        <p className="panel-meta">
          接入外部搜索或知识库 Server。填写启动命令（stdio 传输），例如{" "}
          <code>npx -y @modelcontextprotocol/server-filesystem D:\docs</code>。
          添加时会立即握手验证，连不上则不会保存。
        </p>
        <div className="inline-actions">
          <input
            type="text"
            className="text-input"
            placeholder="启动命令，如：npx -y some-mcp-server --arg"
            value={mcpCommand}
            disabled={mcpBusy}
            onChange={(e) => setMcpCommand(e.target.value)}
            data-od-id="mcp-command-input"
          />
          <button
            type="button"
            className="btn small"
            disabled={mcpBusy || !mcpCommand.trim()}
            onClick={() => void addMcpServer()}
          >
            {mcpBusy ? "连接中…" : "添加并测试"}
          </button>
        </div>
        {mcpError && (
          <p className="error-text" data-od-id="mcp-error">
            {mcpError}
          </p>
        )}
        <p className="panel-meta">
          外部 Server 返回的内容标记为「外部未验证」且需人工复核，不会自动写入正文。
        </p>
      </div>

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
                {c.protocol === MCP_CLIENT_PROTOCOL && (
                  <span className="agent-meta" title={c.endpoint_or_command}>
                    {mcpToolNames(c, mcpTools[c.id])}
                  </span>
                )}
                <span className="inline-actions">
                  {/* 只有出站连接能刷新工具目录；入站连接没有可拉的目录 */}
                  {c.protocol === MCP_CLIENT_PROTOCOL && (
                    <button
                      type="button"
                      className="btn small ghost"
                      disabled={connBusy === c.id}
                      onClick={() => void refreshTools(c.id)}
                    >
                      刷新工具
                    </button>
                  )}
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
