/**
 * 热点面板的「判据层」——把后端 detail 里的语义字段翻成 UI 决策。
 *
 * 为什么单独一层纯函数：这里的判断是**产品红线**（降级必须可见、退化必须提示），
 * 不是样式细节。摆在纯函数里能被 vitest 直接钉死；散在 JSX 里就只能靠肉眼，
 * 而「契约里有字段、UI 没消费」正是本仓最容易复发的一类断裂。
 *
 * 契约口径（两套并存是既成事实，照抄才对）：
 *   - detail 顶层是 **snake_case**：`reason_source` / `brand_applied` /
 *     `trust_level` / `next_step`（见 worker/runtime/results/models.py）
 *   - 列表条目内是 **camelCase**：`reasonSource` / `publishedAt` /
 *     `breakdown.brandFit`（见 hotspot/models.py 的 `to_dict()`）
 *
 * 键名写错**不会报错**，只会静默拿到 `undefined`——所以读取只允许发生在
 * `toRecommendView` / `toBriefView` 两处，其余代码只碰强类型字段。
 */

/* ===== 读取契约 ===== */

export interface ScoreBreakdown {
  freshness: number;
  heat: number;
  brandFit: number;
  novelty: number;
  feedback: number;
  penalty: number;
}

/** 一条推荐项（`recommendations[]` 的元素，camelCase 口径）。 */
export interface RecommendationItem {
  id: string;
  source: string;
  title: string;
  url: string;
  summary: string;
  publishedAt: string;
  score: number;
  breakdown: ScoreBreakdown;
  reason: string;
  /** `"ai"` | `"rule"`——降级必须可见 */
  reasonSource: string;
  risks: string[];
}

export interface RecommendView {
  recommendations: RecommendationItem[];
  count: number;
  /** 参与打分的候选总数（用于说明「从 N 条里挑出 M 条」） */
  considered: number;
  reasonSource: string;
  /** false = 没绑品牌档，闸门不设防（见 brandGateNotice） */
  brandApplied: boolean;
  /** 后端给的降级说明（如「未配置 AI Provider，理由由规则模板生成」） */
  reasonNote: string;
}

/** 选题简报（`ConvertHotspotToTopic` 出参）。 */
export interface BriefView {
  contentVersionId: string;
  hotspotId: string;
  source: string;
  title: string;
  url: string;
  /** 恒为 `external-unverified`（PRD-AGT-003）→ 挂「待复核」标 */
  trustLevel: string;
  reviewState: string;
  /** `"ai"` | `"rule"` | `"none"`——none 表示调用方根本没带理由 */
  reasonSource: string;
  breakdownAttached: boolean;
  reused: boolean;
  /** 简报全文，直接渲染 */
  brief: string;
  nextStep: NextStep | null;
}

export interface NextStep {
  command: string;
  payload: Record<string, unknown>;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function str(source: Record<string, unknown>, key: string): string {
  const v = source[key];
  return typeof v === "string" ? v : "";
}

function num(source: Record<string, unknown>, key: string): number {
  const v = source[key];
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

function bool(source: Record<string, unknown>, key: string): boolean {
  return source[key] === true;
}

function toBreakdown(raw: unknown): ScoreBreakdown {
  const b = asRecord(raw);
  return {
    freshness: num(b, "freshness"),
    heat: num(b, "heat"),
    brandFit: num(b, "brandFit"),
    novelty: num(b, "novelty"),
    feedback: num(b, "feedback"),
    penalty: num(b, "penalty"),
  };
}

function toItem(raw: unknown): RecommendationItem {
  const r = asRecord(raw);
  return {
    id: str(r, "id"),
    source: str(r, "source"),
    title: str(r, "title"),
    url: str(r, "url"),
    summary: str(r, "summary"),
    publishedAt: str(r, "publishedAt"),
    score: num(r, "score"),
    breakdown: toBreakdown(r.breakdown),
    reason: str(r, "reason"),
    reasonSource: str(r, "reasonSource"),
    risks: Array.isArray(r.risks) ? r.risks.filter((x) => typeof x === "string") : [],
  };
}

/** `RecommendHotspots` 的 detail → 强类型视图。键名只在这里读一次。 */
export function toRecommendView(detail: unknown): RecommendView {
  const d = asRecord(detail);
  const raw = Array.isArray(d.recommendations) ? d.recommendations : [];
  const recommendations = raw.map(toItem);
  return {
    recommendations,
    // 后端给的 count 若有就以它为准，缺了才回落到实测长度
    count: typeof d.count === "number" ? d.count : recommendations.length,
    considered: num(d, "considered"),
    reasonSource: str(d, "reason_source"),
    brandApplied: bool(d, "brand_applied"),
    reasonNote: str(d, "reason_note"),
  };
}

/** `ConvertHotspotToTopic` 的 detail → 强类型视图。 */
export function toBriefView(detail: unknown): BriefView {
  const d = asRecord(detail);
  const step = asRecord(d.next_step);
  const command = str(step, "command");
  return {
    contentVersionId: str(d, "content_version_id"),
    hotspotId: str(d, "hotspot_id"),
    source: str(d, "source"),
    title: str(d, "title"),
    url: str(d, "url"),
    trustLevel: str(d, "trust_level"),
    reviewState: str(d, "review_state"),
    reasonSource: str(d, "reason_source"),
    breakdownAttached: bool(d, "breakdown_attached"),
    reused: bool(d, "reused"),
    brief: str(d, "brief"),
    nextStep: command ? { command, payload: asRecord(step.payload) } : null,
  };
}

/* ===== 判据（产品红线） ===== */

export interface Badge {
  label: string;
  hint: string;
}

/**
 * 理由来源标。**红线：`rule` 绝不能当成 AI 洞见展示。**
 *
 * 后端 `_reasons()` 在没配 AI / AI 失败时会降级成规则模板，并如实把
 * `reasonSource` 写成 `rule`——UI 不标出来，用户就会以为那是模型写的。
 */
export function reasonBadge(source: string): Badge {
  if (source === "ai") {
    return { label: "AI 理由", hint: "由模型结合品牌档与热点内容生成" };
  }
  if (source === "rule") {
    return { label: "规则模板", hint: "AI 未参与：这是按打分维度拼出的模板句" };
  }
  return { label: "来源未知", hint: `未识别的理由来源：${source || "(空)"}` };
}

/**
 * 简报侧的理由来源（三态）。`none` 不是「没有理由」，而是**调用方没带**——
 * 转换命令不代猜，UI 也不能替它编。
 */
export function briefReasonLabel(source: string): string {
  if (source === "ai") return "AI 理由";
  if (source === "rule") return "规则模板";
  return "未提供理由（调用方没带，本命令不代猜）";
}

/**
 * 品牌闸门提示。**红线：没绑品牌档必须说，否则用户会以为这就是推荐机制的水平。**
 *
 * 实测过：无品牌档时 `brandFit` 取中性 0.5 → 闸门不设防 → 排序基本等于「热 + 新」。
 * 这是**正确行为**（没有画像就无从判断契合），但不说就是误导。
 */
export function brandGateNotice(brandApplied: boolean): string | null {
  if (brandApplied) return null;
  return "未绑定品牌档：品牌契合闸门不设防，本次推荐已退化为「热 + 新」。绑定品牌档后才有真正的契合判断。";
}

/** 外部未核实素材的标记（PRD-AGT-003）。 */
export function trustBadge(trustLevel: string): string | null {
  if (!trustLevel) return null;
  if (trustLevel === "external-unverified") return "待复核";
  return `待复核（${trustLevel}）`;
}

/** 「从 N 条里挑出 M 条」——推荐是下判断，必须让人看见分母。 */
export function candidateLine(count: number, considered: number): string {
  return `从 ${considered} 条候选里挑出 ${count} 条`;
}

/** 空结果的解释。**红线：空面板不是空状态**，必须说清是没数据还是被筛掉了。 */
export function emptyHint(considered: number): string {
  if (considered === 0) {
    return "还没有热点入库。先执行「抓取热点」，把上游源的榜单拉下来。";
  }
  return `已对 ${considered} 条候选打分，但没有一条达到展示门槛——多半被品牌闸门或禁用词压下去了。`;
}

export function reusedNotice(reused: boolean): string | null {
  return reused ? "同内容简报已存在，本次直接复用（未新增版本）" : null;
}

/** 打分口径。**乘性公式不能展示成加总**，否则解释全部失真。 */
export const SCORE_FORMULA = "总分 = 核心分 × 品牌闸门 × (1 − 惩罚) × 100";

export type BreakdownKind = "core" | "gate" | "penalty";

export interface BreakdownRow {
  key: keyof ScoreBreakdown;
  label: string;
  kind: BreakdownKind;
  value: number;
  percent: number;
}

const _ROWS: { key: keyof ScoreBreakdown; label: string; kind: BreakdownKind }[] = [
  { key: "freshness", label: "时效新", kind: "core" },
  { key: "heat", label: "同源热度", kind: "core" },
  { key: "novelty", label: "新颖度", kind: "core" },
  { key: "feedback", label: "历史反馈", kind: "core" },
  { key: "brandFit", label: "品牌契合（闸门）", kind: "gate" },
  { key: "penalty", label: "惩罚（禁用词等）", kind: "penalty" },
];

export function breakdownRows(b: ScoreBreakdown): BreakdownRow[] {
  return _ROWS.map((row) => ({
    ...row,
    value: b[row.key],
    percent: Math.round(b[row.key] * 100),
  }));
}

/**
 * 下一步提示。**payload 键名直接取自契约里的 `next_step`**，不让 UI 手写 ——
 * `GenerateTopic` 的 spec 是 `Spec(**env.payload)` 无别名，必须吃
 * `source_version_id`；手写成 `sourceVersionId` 是当场 `INVALID_ARGUMENT`。
 */
export function nextStepHint(step: NextStep | null): string | null {
  if (!step) return null;
  const keys = Object.keys(step.payload);
  if (keys.length === 0) return `下一步：${step.command}`;
  return `下一步：${step.command}（${keys.map((k) => `${k}=…`).join(", ")}）`;
}
