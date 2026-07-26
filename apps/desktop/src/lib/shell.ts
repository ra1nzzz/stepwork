/**
 * 本地路径打开辅助（Tranche 2）
 * - Tauri 环境：@tauri-apps/plugin-opener 的 openPath()（capability: opener:allow-open-path）
 * - 浏览器环境：无法打开本地路径，回退为复制到剪贴板并抛出提示性错误
 */

import { isTauri } from "@/lib/tauri";

/** 取路径所在目录（Windows / POSIX 分隔符都兼容） */
export function dirname(path: string): string {
  const normalized = path.replace(/^file:\/\//, "");
  const idx = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
  return idx > 0 ? normalized.slice(0, idx) : normalized;
}

/**
 * 用系统默认程序打开本地路径（文件或目录）。
 * 浏览器环境退化为复制路径到剪贴板，并抛错让调用方展示提示。
 */
export async function openLocalPath(path: string): Promise<void> {
  const target = path.replace(/^file:\/\//, "");
  if (isTauri()) {
    const { openPath } = await import("@tauri-apps/plugin-opener");
    await openPath(target);
    return;
  }
  await navigator.clipboard.writeText(target);
  throw new Error("浏览器环境无法打开本地路径，已复制到剪贴板");
}
