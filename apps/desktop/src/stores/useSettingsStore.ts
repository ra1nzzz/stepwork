/**
 * SET.2 — 设置 store（zustand + persist）
 *
 * 安全模型（强制）：
 * - API Key（aiApiKey / asrApiKey / ttsApiKey）等以 `Key` / `Secret` 结尾的字段
 *   **绝不**写入 localStorage。`persist` 的 `partialize` 会递归剔除这些字段。
 * - store 内存中可暂存 key 值（供「保存」时上行），但持久化层永远拿不到它们。
 * - 不提供任何回显明文 key 的 getter；外部只应通过 `update` 写入、`settings` 读取，
 *   且 UI 层一律以 password 控件呈现。
 */
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

export type LlmProvider = "cloud" | "openai-compatible" | "ollama";
export type AsrProvider = "local" | "cloud";
export type TtsProvider = "local" | "cloud";
export type ExportFormat = "MP4" | "SRT" | "WAV";
export type Theme = "dark" | "light";
export type LogLevel = "debug" | "info" | "warn" | "error";

export interface LlmConfig {
  provider: LlmProvider;
  model: string;
  apiKey: string;
  baseUrl: string;
  costPer1k: string;
  /** 采样参数（SET.3 AI Provider Tab 绑定） */
  sampling: { temperature: number; topP: number; maxTokens: number };
}
export interface AsrConfig {
  provider: AsrProvider;
  apiKey: string;
  baseUrl: string;
}
export interface TtsConfig {
  provider: TtsProvider;
  apiKey: string;
  baseUrl: string;
  model: string;
}
export interface WorkspaceConfig {
  /** 项目默认文件夹 / STEPWORK_HOME */
  defaultPath: string;
}
export interface BrandConfig {
  name: string;
  audience: string;
  tone: string;
  mustExecute: string[];
  defaultOutput: string[];
}
export interface DataConfig {
  retentionDays: number;
  desensitize: boolean;
  projectDelete: boolean;
  uploadScope: string;
}
export interface ExportConfig {
  format: ExportFormat;
  checkDeps: boolean;
}
export interface UiConfig {
  theme: Theme;
  language: string;
  logLevel: LogLevel;
}

export interface SettingsConfig {
  llm: LlmConfig;
  asr: AsrConfig;
  tts: TtsConfig;
  workspace: WorkspaceConfig;
  brand: BrandConfig;
  data: DataConfig;
  export: ExportConfig;
  ui: UiConfig;
}

const DEFAULT_SETTINGS: SettingsConfig = {
  llm: {
    provider: "cloud",
    model: "step-3.7",
    apiKey: "",
    baseUrl: "",
    costPer1k: "0.012",
    sampling: { temperature: 0.7, topP: 0.9, maxTokens: 2048 },
  },
  asr: { provider: "cloud", apiKey: "", baseUrl: "" },
  tts: { provider: "cloud", apiKey: "", baseUrl: "", model: "StepAudio" },
  workspace: { defaultPath: "" },
  brand: {
    name: "科技实测 · 克制判断",
    audience: "关注 AI 产品与效率工具的内容用户",
    tone: "第一人称验证；结论先于功能；避免绝对化判断；明确个人样本范围；不使用未核验的性能数字。",
    mustExecute: ["cite-sources", "check-similarity", "human-confirm-risk"],
    defaultOutput: ["<=90s", "9:16"],
  },
  data: { retentionDays: 30, desensitize: true, projectDelete: false, uploadScope: "" },
  export: { format: "MP4", checkDeps: true },
  ui: { theme: "dark", language: "zh-CN", logLevel: "info" },
};

export interface SettingsState {
  settings: SettingsConfig;
  savedAt: string | null;
  /** 深合并补丁（按 section 合并，不覆盖未提供的字段） */
  update: (patch: Partial<SettingsConfig>) => void;
  reset: () => void;
  markSaved: () => void;
}

/** 深合并：对象键递归合并，数组/原始值整体替换 */
function deepMerge<T>(base: T, patch: Partial<T>): T {
  // 数组 / 基本类型：整体替换（配置项里的数组是用户显式选择的整体集合）
  if (Array.isArray(base) || Array.isArray(patch)) {
    return (patch ?? base) as unknown as T;
  }
  const out: Record<string, unknown> = { ...(base as Record<string, unknown>) };
  for (const key of Object.keys(patch) as (keyof T)[]) {
    const value = patch[key];
    if (value === undefined) continue;
    const isObj = value !== null && typeof value === "object" && !Array.isArray(value);
    if (isObj) {
      out[key as string] = deepMerge(
        (base as Record<string, unknown>)[key as string] ?? {},
        value as Record<string, unknown>,
      );
    } else {
      out[key as string] = value as unknown;
    }
  }
  return out as T;
}

/** 匹配需剔除的密钥字段：以 Key 结尾（排除 passkey）/ secret / token /
 *  password / passphrase / credential（不区分大小写）。覆盖非约定名密钥
 *  （如 ``llm.token``），同时避免误删 ``passkey`` 这类非密钥字段。 */
const SECRET_RE = /(?<!pass)key$|secret$|token$|password$|passphrase$|credential$/i;
function isSecretKey(key: string): boolean {
  return SECRET_RE.test(key);
}

/** 递归剔除所有密钥字段，返回可持久化的纯净对象 */
function stripSecrets<T>(value: T): T {
  if (Array.isArray(value)) return value.map((v) => stripSecrets(v)) as unknown as T;
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      if (isSecretKey(k)) continue;
      out[k] = stripSecrets(v);
    }
    return out as unknown as T;
  }
  return value;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      settings: DEFAULT_SETTINGS,
      savedAt: null,
      update: (patch) =>
        set((state) => ({
          settings: deepMerge(state.settings, patch),
          savedAt: null,
        })),
      reset: () => set({ settings: DEFAULT_SETTINGS, savedAt: null }),
      markSaved: () => set({ savedAt: new Date().toISOString() }),
    }),
    {
      name: "stepwork-settings",
      storage: createJSONStorage(() => localStorage),
      // 持久化剔除所有密钥字段：localStorage 中永不出现 apiKey / *Secret。
      partialize: (state) => ({
        settings: stripSecrets(state.settings),
        savedAt: state.savedAt,
      }),
      // 重新水合时把持久化配置深合并回默认值，保证被剔除的 apiKey 等回落到默认空串。
      merge: (persisted, current) => {
        const p = (persisted ?? {}) as Partial<Pick<SettingsState, "settings" | "savedAt">>;
        return {
          ...current,
          settings: deepMerge(DEFAULT_SETTINGS, p.settings ?? {}),
          savedAt: p.savedAt ?? null,
        };
      },
    },
  ),
);
