/**
 * 设置页各 Panel 共享的常量、类型与小工具。
 *
 * 这些原本散在 1430 行的 SettingsView 里；Panel 拆成独立文件后必须有明确的
 * 共享落点，否则会被各 Panel 各抄一份。
 */

import type { BrandProfile } from "@/lib/types";

export const MUST_EXECUTE_OPTIONS: { value: string; label: string }[] = [
  { value: "cite-sources", label: "标注事实来源与时间戳" },
  { value: "check-similarity", label: "检查历史内容相似度" },
  { value: "human-confirm-risk", label: "高风险判断需人工确认" },
];

export const DEFAULT_OUTPUT_OPTIONS: { value: string; label: string }[] = [
  { value: "<=90s", label: "90 秒以内" },
  { value: "9:16", label: "9:16 竖屏" },
  { value: "voiceover+broll", label: "口播 + B-roll" },
];

export interface BrandProfileForm {
  name: string;
  positioning: string;
  audience: string;
  tone: string;
  contentPillars: string[];
  bannedExpressions: string[];
}

export function toggleInList(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export function emptyBrandForm(): BrandProfileForm {
  return {
    name: "",
    positioning: "",
    audience: "",
    tone: "",
    contentPillars: [],
    bannedExpressions: [],
  };
}

export function profileToForm(p: BrandProfile): BrandProfileForm {
  return {
    name: p.name ?? "",
    positioning: p.positioning ?? "",
    audience: p.audience ?? "",
    tone: p.tone ?? "",
    contentPillars: Array.isArray(p.contentPillars) ? p.contentPillars : [],
    bannedExpressions: Array.isArray(p.bannedExpressions) ? p.bannedExpressions : [],
  };
}
