import { useHealthStore } from "@/stores/useHealthStore";
import type { SidecarError, SidecarErrorKind } from "@/lib/types";

interface ErrorGuideCardProps {
  error: SidecarError;
}

interface GuideContent {
  icon: string;
  title: string;
  description: string;
  command: string | null;
  showRestart: boolean;
  showStderr: boolean;
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

function guideFor(error: SidecarError): GuideContent {
  const kind: SidecarErrorKind = error.kind;
  switch (kind) {
    case "PythonMissing":
      return {
        icon: "PY",
        title: "未找到 Python 环境",
        description:
          "STEPWORK 依赖本地 Python 3.12+ 运行 Worker Sidecar。请在仓库根目录执行以下命令初始化虚拟环境，然后重启应用。",
        command:
          "python -m venv .venv\n.venv\\Scripts\\activate\npip install -e worker/",
        showRestart: true,
        showStderr: false,
      };
    case "SpawnFailed":
      return {
        icon: "SP",
        title: "Worker 进程启动失败",
        description:
          "Tauri 已找到 Python，但 Worker 子进程在启动阶段退出。下方为标准错误输出（截断前 200 字符），可用于定位问题。",
        command: null,
        showRestart: true,
        showStderr: true,
      };
    case "HandshakeTimeout":
      return {
        icon: "HS",
        title: "Worker 握手超时",
        description:
          "Worker 启动后 10 秒内未发送 runtime.ready。请尝试在终端独立运行 Worker，确认是否能正常输出 ready 帧。",
        command: "python -m worker.runtime",
        showRestart: true,
        showStderr: false,
      };
    case "RpcProtocolError":
      return {
        icon: "RPC",
        title: "RPC 协议不匹配",
        description:
          "前端 / 宿主 / Worker 之间的 JSON-RPC 帧格式或协议版本不一致。通常由版本错配导致，建议重启应用；若反复出现，请升级到匹配的 Worker 版本。",
        command: null,
        showRestart: true,
        showStderr: false,
      };
    case "WorkerCrashed":
      return {
        icon: "CR",
        title: "Worker 进程异常退出",
        description:
          "Worker 在运行过程中崩溃。系统会按指数退避自动重启；若持续崩溃，请查看日志定位根因。",
        command: null,
        showRestart: true,
        showStderr: true,
      };
    case "FrameTooLarge":
      return {
        icon: "FR",
        title: "RPC 帧超过大小上限",
        description:
          "收到长度前缀 >1MB 的 RPC 帧，已按协议丢弃。通常是 Worker 发送了异常数据，建议重启。",
        command: null,
        showRestart: true,
        showStderr: false,
      };
    case "ParseError":
      return {
        icon: "PE",
        title: "RPC 帧解析失败",
        description:
          "收到的帧不是合法 JSON。按协议已关闭连接触发重启；若持续出现，说明 Worker 输出了非协议内容。",
        command: null,
        showRestart: true,
        showStderr: true,
      };
    case "Shutdown":
      return {
        icon: "SD",
        title: "Worker 已关闭",
        description: "Worker 已按请求优雅退出。点击下方按钮重新启动。",
        command: null,
        showRestart: true,
        showStderr: false,
      };
    case "Unknown":
    default:
      return {
        icon: "?",
        title: "未知错误",
        description:
          "发生未分类的 Sidecar 错误。请复制诊断信息并提交 Issue，或尝试重启应用。",
        command: null,
        showRestart: true,
        showStderr: false,
      };
  }
}

/**
 * 错误引导卡（v1.1 Patch-U2）
 * 按 SidecarErrorKind 渲染差异化修复指引
 */
export function ErrorGuideCard({ error }: ErrorGuideCardProps) {
  const restart = useHealthStore((s) => s.restart);
  const fetchHealth = useHealthStore((s) => s.fetchHealth);
  const guide = guideFor(error);

  const stderr =
    error.details && typeof error.details.stderr === "string"
      ? truncate(error.details.stderr, 200)
      : null;

  return (
    <article className="error-guide-card" data-od-id="error-guide-card">
      <div className="error-guide-icon" aria-hidden="true">
        {guide.icon}
      </div>
      <div className="error-guide-kind">
        {error.kind} · {error.code}
      </div>
      <h1 className="error-guide-title">{guide.title}</h1>
      <p className="error-guide-desc">{guide.description}</p>
      <p className="error-guide-desc">
        <strong>错误信息：</strong>
        {error.message}
      </p>

      {guide.command && (
        <code className="error-guide-code">{guide.command}</code>
      )}
      {guide.showStderr && stderr && (
        <code className="error-guide-code">{stderr}</code>
      )}
      {error.correlation_id && (
        <p className="error-guide-desc">
          <strong>Correlation ID：</strong>
          {error.correlation_id}
        </p>
      )}

      <div className="error-guide-actions">
        {guide.showRestart && (
          <button
            type="button"
            className="btn primary"
            onClick={() => void restart()}
            data-od-id="error-restart"
          >
            重启 Worker
          </button>
        )}
        <button
          type="button"
          className="btn ghost"
          onClick={() => void fetchHealth()}
          data-od-id="error-retry"
        >
          重新检测
        </button>
      </div>
    </article>
  );
}
