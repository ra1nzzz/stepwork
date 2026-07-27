import { useState } from "react";
import { useHealthStore } from "@/stores/useHealthStore";
import { resetBridgeDetection } from "@/lib/tauri";

interface DetailItem {
  label: string;
  value: string;
}

function fmtUptime(seconds: number): string {
  const s = Math.floor(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function fmtTimestamp(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString("zh-CN", { hour12: false });
  } catch {
    return iso;
  }
}

function runtimeString(
  info: Record<string, unknown>,
  key: string,
): string | null {
  const v = info[key];
  return typeof v === "string" && v.length > 0 ? v : null;
}

function isBackendDisconnected(health: { status: string; degraded_reasons: string[] }): boolean {
  return health.status === "down" && health.degraded_reasons.includes("backend_not_connected");
}

/**
 * 核心引擎状态卡片
 * - 顶部：标题 + 状态徽章 + 大字状态文本
 * - 中部：2 列详情 grid（版本/PID/启动耗时/心跳/活跃任务/Python/SQLite）
 * - degraded：渲染 degraded_reasons 列表（v1.1 Patch-U3）
 * - 底部：重启 Worker / 复制诊断信息 / 重连检测
 */
export function HealthCard() {
  const health = useHealthStore((s) => s.health);
  const restart = useHealthStore((s) => s.restart);
  const fetchHealth = useHealthStore((s) => s.fetchHealth);
  const [isRestarting, setIsRestarting] = useState(false);
  const [copied, setCopied] = useState(false);

  if (!health) return null;

  const status = health.status;
  const disconnected = isBackendDisconnected(health);

  const statusText = disconnected
    ? "未连接到后端"
    : status === "ok"
      ? "核心引擎运行正常"
      : status === "degraded"
        ? "核心引擎性能下降"
        : "核心引擎已停止";

  const pythonVersion = runtimeString(health.runtime_info, "python_version");
  const sqliteVersion = runtimeString(health.runtime_info, "sqlite_version");

  const details: DetailItem[] = [
    { label: "Worker 版本", value: health.version },
    { label: "协议版本", value: health.protocol_version },
    { label: "PID", value: String(health.pid) },
    { label: "启动耗时", value: `${health.startup_duration_ms} ms` },
    { label: "最近心跳", value: fmtTimestamp(health.last_heartbeat_at) },
    { label: "活跃任务", value: String(health.active_jobs) },
    { label: "运行时长", value: fmtUptime(health.uptime_seconds) },
    { label: "SQLite 版本", value: sqliteVersion ?? "—" },
  ];
  if (pythonVersion) {
    details.push({ label: "Python 版本", value: pythonVersion });
  }

  const handleRestart = async () => {
    if (isRestarting) return;
    setIsRestarting(true);
    try {
      await restart();
    } catch {
      // store 内部已记录 error
    } finally {
      // 5s 防连点
      setTimeout(() => setIsRestarting(false), 5000);
    }
  };

  const handleRetryConnection = async () => {
    resetBridgeDetection();
    await fetchHealth();
  };

  const handleCopyDiagnostics = async () => {
    const payload = JSON.stringify(health, null, 2);
    try {
      await navigator.clipboard.writeText(payload);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // 剪贴板不可用（非 secure context 等）：降级到 textarea 方案
      const ta = document.createElement("textarea");
      ta.value = payload;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } finally {
        document.body.removeChild(ta);
      }
    }
  };

  return (
    <article className="health-card" data-od-id="health-card">
      <header className="health-card-head">
        <h1 className="health-card-title">核心引擎状态</h1>
        <span className="status-badge" data-status={status}>
          {status}
        </span>
      </header>

      <p className="health-status-text">{statusText}</p>

      {disconnected && (
        <section className="degraded-reasons" aria-label="连接提示" data-od-id="connection-guide">
          <div className="degraded-reasons-title">连接提示</div>
          <p>当前未连接到后端 Worker。</p>
          <ul>
            <li>桌面环境：请确保 Worker 进程已正常启动</li>
            <li>浏览器开发：请运行 python worker/dev_bridge.py 启动开发桥接</li>
          </ul>
        </section>
      )}

      {status === "degraded" && health.degraded_reasons.length > 0 && (
        <section
          className="degraded-reasons"
          aria-label="性能下降原因"
          data-od-id="degraded-reasons"
        >
          <div className="degraded-reasons-title">Degraded Reasons</div>
          <ul>
            {health.degraded_reasons.map((reason, idx) => (
              <li key={`${idx}-${reason}`}>{reason}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="health-detail-grid" aria-label="引擎详情">
        {details.map((d) => (
          <div className="health-detail-item" key={d.label}>
            <span className="health-detail-label">{d.label}</span>
            <span className="health-detail-value">{d.value}</span>
          </div>
        ))}
      </section>

      <div className="health-actions">
        {disconnected && (
          <button
            type="button"
            className="btn primary"
            onClick={() => void handleRetryConnection()}
            data-od-id="retry-connection"
          >
            重试连接
          </button>
        )}
        {(status === "down" || status === "degraded") && !disconnected && (
          <button
            type="button"
            className="btn primary"
            onClick={() => void handleRestart()}
            disabled={isRestarting}
            data-od-id="restart-worker"
          >
            {isRestarting ? "重启中…" : "重启 Worker"}
          </button>
        )}
        <button
          type="button"
          className="btn ghost"
          onClick={() => void handleCopyDiagnostics()}
          data-od-id="copy-diagnostics"
        >
          {copied ? "已复制" : "复制诊断信息"}
        </button>
      </div>
    </article>
  );
}
