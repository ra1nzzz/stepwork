/**
 * 执行前费用透明 Hook（Tranche 2 · PRD-ANA-006）
 *
 * 从 GetConfig 的掩码视图读取解析后的 provider / model 与
 * llm.costPer1k（估算单价），供分析/渲染视图在任务开始前展示
 * 「将使用的模型 + 预计费用」。密钥永不经过此处（掩码视图无明文）。
 */

import { useEffect, useState } from "react";
import { getConfig } from "@/lib/tauri";

export interface ProviderInfo {
  ai: { provider: string | null; model: string | null };
  tts: { provider: string | null; model: string | null };
  /** llm.costPer1k（字符串配置，解析失败为 null） */
  costPer1k: number | null;
  /** tts.costPer1k（PRD-REN-002 旁白合成单价；解析失败为 null） */
  ttsCostPer1k: number | null;
}

/** 按字符量粗估费用：chars / 1000 * costPer1k（无单价时 null） */
export function estimateCost(chars: number, costPer1k: number | null): number | null {
  if (costPer1k == null || !Number.isFinite(costPer1k)) return null;
  return (chars / 1000) * costPer1k;
}

/** 费用格式化（保留 4 位小数；null → "未知"） */
export function formatCost(cost: number | null): string {
  if (cost == null) return "未知";
  return `¥${cost.toFixed(4)}`;
}

export function useProviderInfo(): ProviderInfo | null {
  const [info, setInfo] = useState<ProviderInfo | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await getConfig();
        if (cancelled || !res.ok) return;
        const resolved = res.resolved;
        const cfg = (res.config ?? {}) as {
          llm?: { costPer1k?: unknown };
          tts?: { costPer1k?: unknown };
        };
        const parsed = Number.parseFloat(String(cfg.llm?.costPer1k ?? ""));
        const ttsParsed = Number.parseFloat(String(cfg.tts?.costPer1k ?? ""));
        setInfo({
          ai: {
            provider: resolved?.ai.provider ?? null,
            model: resolved?.ai.model ?? null,
          },
          tts: {
            provider: resolved?.tts.provider ?? null,
            model: resolved?.tts.model ?? null,
          },
          costPer1k: Number.isFinite(parsed) ? parsed : null,
          ttsCostPer1k: Number.isFinite(ttsParsed) ? ttsParsed : null,
        });
      } catch {
        /* 后端未连接：不展示预估信息 */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return info;
}
