/**
 * Tauri API 封装
 * - 在 Tauri 环境中走 @tauri-apps/api invoke
 * - 在纯浏览器环境（npm run dev）下返回 mock 数据，便于前端独立调试（P1-UX #1）
 */

import type { AppInfo, HealthStatus } from "./types";

export const isTauri = (): boolean =>
  typeof window !== "undefined" && "__TAURI__" in window;

/**
 * Dev bridge 模式（P1）：当以 `VITE_DEV_BRIDGE=1 npm run dev` 启动时，
 * 浏览器版 GUI 不再走 mock，而是把命令转发到本地 Python dev_bridge
 * （worker/dev_bridge.py），从而调用**真实**的 worker Command Bus。
 * 这在没有 Rust sidecar 的纯前端开发/沙箱环境里，让 MVP 功能真正可用。
 */
export const DEV_BRIDGE = import.meta.env.VITE_DEV_BRIDGE === "1";
const DEV_BRIDGE_URL =
  (import.meta.env.VITE_DEV_BRIDGE_URL as string | undefined) ??
  "http://127.0.0.1:8787";

type InvokeFn = <T>(cmd: string, args?: Record<string, unknown>) => Promise<T>;

let cachedInvoke: InvokeFn | null = null;

async function getInvoke(): Promise<InvokeFn> {
  if (cachedInvoke) return cachedInvoke;
  const mod = await import("@tauri-apps/api/core");
  cachedInvoke = mod.invoke as InvokeFn;
  return cachedInvoke;
}

const MOCK_STARTED_AT = Date.now();

function mockHealth(): HealthStatus {
  const uptime = Math.floor((Date.now() - MOCK_STARTED_AT) / 1000);
  return {
    status: "ok",
    version: "0.1.0-mock",
    protocol_version: "1",
    uptime_seconds: uptime,
    pid: 42424,
    last_heartbeat_at: new Date().toISOString(),
    startup_duration_ms: 832,
    active_jobs: 0,
    degraded_reasons: [],
    runtime_info: {
      python_version: "3.13.0 (mock)",
      sqlite_version: "3.45.1",
      platform: "browser-mock",
    },
  };
}

function mockAppInfo(): AppInfo {
  return {
    version: "0.1.0-mock",
    platform: "browser",
    stepwork_home: "~/STEPWORK (mock)",
    restart_count: 0,
    last_crash_at: null,
  };
}

export async function getWorkerHealth(): Promise<HealthStatus> {
  if (DEV_BRIDGE) {
    try {
      const res = await fetch(`${DEV_BRIDGE_URL}/health`);
      if (res.ok) return (await res.json()) as HealthStatus;
    } catch {
      /* 桥未启动：返回降级健康，UI 仍可渲染 */
    }
    return {
      status: "degraded",
      version: "0.1.0-bridge-offline",
      protocol_version: "1",
      uptime_seconds: 0,
      pid: 0,
      last_heartbeat_at: new Date().toISOString(),
      startup_duration_ms: 0,
      active_jobs: 0,
      degraded_reasons: ["dev_bridge_offline"],
      runtime_info: { python_version: "n/a", sqlite_version: "n/a", platform: "browser-devbridge" },
    };
  }
  if (!isTauri()) {
    // 模拟网络/进程延迟
    await new Promise((r) => setTimeout(r, 220));
    return mockHealth();
  }
  const invoke = await getInvoke();
  return invoke<HealthStatus>("get_worker_health");
}

export async function restartWorker(): Promise<void> {
  if (!isTauri()) {
    await new Promise((r) => setTimeout(r, 800));
    return;
  }
  const invoke = await getInvoke();
  return invoke<void>("restart_worker");
}

export async function getAppInfo(): Promise<AppInfo> {
  if (!isTauri()) {
    return mockAppInfo();
  }
  const invoke = await getInvoke();
  return invoke<AppInfo>("get_app_info");
}

/**
 * ===== W3 / W4 Command Bus 桥接（Batch3） =====
 *
 * 所有 W3/W4 命令（ImportSource / TranscribeSource / AnalyzeSource）
 * 都经由 Rust `dispatch_command` 单点转发到 worker 的 Command Bus。
 * 真实 Tauri 环境走 invoke；纯浏览器环境返回 mock 以便独立调试。
 */

import type { CommandEnvelope, CommandResult, ConfigResult } from "./types";
import { useSettingsStore } from "@/stores/useSettingsStore";
import type { SettingsConfig } from "@/stores/useSettingsStore";

/** 浏览器/mock 环境的默认工作区 id */
export const DEFAULT_WORKSPACE_ID = "ws-local";

/** 当前工作区 id（单工作区占位；未来接入真实 workspace 上下文时替换此函数）。 */
export function getWorkspaceId(): string {
  return DEFAULT_WORKSPACE_ID;
}

/** 轻量 uuid（浏览器 crypto.randomUUID 优先，降级到时间+随机） */
function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 构造一个符合 command-envelope.schema.json 的信封（泛型透传已类型化的 payload） */
export function buildEnvelope<T>(
  commandType: CommandEnvelope["commandType"],
  workspaceId: string,
  projectId: string | null,
  payload: T,
): CommandEnvelope {
  return {
    commandId: uuid(),
    commandType,
    schemaVersion: "1",
    actor: { type: "desktop", id: "ui" },
    source: "ui",
    workspaceId,
    projectId,
    idempotencyKey: null,
    payload: payload as unknown as Record<string, unknown>,
    requestedAt: new Date().toISOString(),
  };
}

/** 转发信封到 worker Command Bus（经 Rust dispatch_command） */
export async function dispatchCommand(
  envelope: CommandEnvelope,
): Promise<CommandResult> {
  if (DEV_BRIDGE) {
    try {
      const res = await fetch(`${DEV_BRIDGE_URL}/dispatch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(envelope),
      });
      if (!res.ok) {
        const txt = await res.text();
        return {
          ok: false,
          commandId: envelope.commandId,
          job_id: null,
          artifact_ids: [],
          error: `DEV_BRIDGE_HTTP_${res.status}`,
          detail: { http_body: txt },
        };
      }
      return (await res.json()) as CommandResult;
    } catch (e) {
      return {
        ok: false,
        commandId: envelope.commandId,
        job_id: null,
        artifact_ids: [],
        error: "DEV_BRIDGE_OFFLINE",
        detail: { cause: String(e) },
      };
    }
  }
  if (!isTauri()) {
    await new Promise((r) => setTimeout(r, 300));
    return mockDispatchResult(envelope);
  }
  const invoke = await getInvoke();
  return invoke<CommandResult>("dispatch_command", { envelope });
}

/**
 * 浏览器 mock：导入 / 转写默认成功（无密钥也可演示流程）；
 * 分析默认失败（需要真实密钥），借此暴露失败重试 UI 路径。
 * W5：GenerateTopic 返回示例角度；GenerateScript/SaveScript 返回示例脚本，
 * 使选题→脚本→编辑器链路在纯浏览器下也可演示。
 */
function mockDispatchResult(env: CommandEnvelope): CommandResult {
  if (env.commandType === "AnalyzeSource") {
    return {
      ok: false,
      commandId: env.commandId,
      job_id: null,
      artifact_ids: [],
      error: "MOCK_NO_PROVIDER",
      detail: null,
    };
  }
  return {
    ok: true,
    commandId: env.commandId,
    job_id: `job-${uuid()}`,
    artifact_ids: [`cv-${uuid()}`],
    error: null,
    detail: mockDetail(env.commandType),
  };
}

function mockDetail(commandType: CommandEnvelope["commandType"]): Record<string, unknown> {
  if (commandType === "GenerateTopic") {
    return {
      mock: true,
      angles: [
        { id: "a1", title: "角度一：反常识开场", rationale: "用冲突感抓住注意力", hook: "你以为…其实…" },
        { id: "a2", title: "角度二：实用清单", rationale: "可收藏的干货结构", hook: "3 个立刻能用的招" },
        { id: "a3", title: "角度三：真实故事", rationale: "情绪共鸣驱动转发", hook: "我朋友亲身经历…" },
      ],
    };
  }
  if (commandType === "GenerateScript" || commandType === "SaveScript") {
    return {
      mock: true,
      title: "示例短视频脚本",
      parent: null,
      script: {
        title: "示例短视频脚本",
        body: "（镜头 0-3s）钩子：你以为剪视频很难？\n（3-10s）其实只要三步…\n（10-15s）关注我，下期拆解。",
      },
    };
  }
  if (commandType === "CreateRenderJob") {
    return {
      mock: true,
      video_uri: "file:///mock/render/out.mp4",
      template: "vertical-caption-v1",
      tts_engine: "synthesize",
    };
  }
  if (commandType === "CancelJob") {
    return { mock: true, cancelled: true };
  }
  // W8：溯源 / Agent / 诊断 / 插件——MVP 阶段返回空态结构（不含虚假记录），
  // 诊断包导出返回 plausible 路径以便 UI 演示结果展示。
  if (commandType === "GetProvenance") {
    return { mock: true, records: [] };
  }
  if (commandType === "ListAgentTasks") {
    return { mock: true, tasks: [] };
  }
  if (commandType === "ListAgentArtifacts") {
    return { mock: true, artifacts: [] };
  }
  if (commandType === "ExportDiagnosticsBundle") {
    return {
      mock: true,
      bundle_path: `file:///mock/diagnostics/stepwork-bundle-${Date.now()}.zip`,
      size_bytes: 0,
      desensitized: true,
    };
  }
  if (commandType === "ListPlugins") {
    return { mock: true, plugins: [] };
  }
  if (commandType === "EnablePlugin") {
    return { mock: true, enabled: true };
  }
  if (commandType === "DisablePlugin") {
    return { mock: true, enabled: false };
  }
  return { mock: true };
}

/* ===== SET.4 设置页配置桥接 ===== */

/** 本地推导解析摘要（mock 模式用）。 */
function deriveResolved(s: SettingsConfig): ConfigResult["resolved"] {
  return {
    ai: { provider: s.llm.provider, model: s.llm.model, hasKey: !!s.llm.apiKey },
    asr: { provider: s.asr.provider, hasKey: !!s.asr.apiKey },
    tts: { provider: s.tts.provider, model: s.tts.model, hasKey: !!s.tts.apiKey },
  };
}

/** 把后端 CommandResult（detail={config,resolved}）适配为前端 ConfigResult。 */
function adaptConfig(res: CommandResult): ConfigResult {
  if (!res.ok) return { ok: false, error: res.error ?? "bridge_error" };
  const detail = (res.detail ?? {}) as {
    config?: Record<string, unknown>;
    resolved?: ConfigResult["resolved"];
  };
  return { ok: true, config: detail.config, resolved: detail.resolved };
}

/** 经 dev-bridge 转发配置命令到真实 worker Command Bus。 */
async function bridgeConfig(
  commandType: CommandEnvelope["commandType"],
  payload: Record<string, unknown>,
): Promise<ConfigResult> {
  const env = buildEnvelope(commandType, getWorkspaceId(), null, payload);
  const res = await dispatchCommand(env);
  return adaptConfig(res);
}

/** 读取合并后的配置（掩码视图）+ 解析摘要。 */
export async function getConfig(): Promise<ConfigResult> {
  if (DEV_BRIDGE) return bridgeConfig("GetConfig", {});
  if (!isTauri()) {
    const s = useSettingsStore.getState().settings;
    return {
      ok: true,
      config: s as unknown as Record<string, unknown>,
      resolved: deriveResolved(s),
    };
  }
  const env = buildEnvelope("GetConfig", getWorkspaceId(), null, {});
  const invoke = await getInvoke();
  return adaptConfig(await invoke<CommandResult>("dispatch_command", { envelope: env }));
}

/** 保存配置：非密钥落 Workspace.settings；密钥仅进内存覆盖层（后端剥离+内存）。 */
export async function updateConfig(settings: SettingsConfig): Promise<ConfigResult> {
  const payload = settings as unknown as Record<string, unknown>;
  if (DEV_BRIDGE) return bridgeConfig("UpdateConfig", payload);
  if (!isTauri()) {
    // mock：仅更新内存 store（persist 的 partialize 会剔除密钥）
    useSettingsStore.getState().update({ ...settings });
    return { ok: true };
  }
  const env = buildEnvelope("UpdateConfig", getWorkspaceId(), null, payload);
  const invoke = await getInvoke();
  return adaptConfig(await invoke<CommandResult>("dispatch_command", { envelope: env }));
}
