/**
 * 发布通道（Provider）状态的「判据层」——把 `ProbePublishProvider` 的 detail
 * 翻成 UI 决策。
 *
 * 为什么单独一层纯函数：与热点面板同一个理由 —— 这里的判断是**产品红线**，
 * 不是样式细节。摆在纯函数里能被 vitest 钉死；散在 JSX 里就只能靠肉眼，
 * 而「契约里有字段、UI 没消费」正是本仓最容易复发的一类断裂。
 *
 * 契约口径：detail 顶层是 **snake_case**（`exit_code` / `auto_publish`），与
 * `worker/runtime/results/models.py` 的 `ProbePublishProviderDetail` 对齐。
 * 键名写错**不会报错**，只会静默拿到 `undefined` —— 所以读取只允许发生在
 * `toProviderStatus` 一处，其余代码只碰强类型字段。
 */

/* ===== 读取契约 ===== */

/** 三态 + `unknown`。后端只保证三个值，`unknown` 是防御契约漂移用的。 */
export type ProviderState = "ready" | "unavailable" | "need_login" | "unknown";

export interface ProviderStatusView {
  state: ProviderState;
  /** 后端返回的原始 state 字符串 —— 未识别时用于**如实显示**，不吞掉 */
  rawState: string;
  /** 探的是哪个渠道；未配置时为 "" */
  provider: string;
  /** 现在是什么状态（后端原文，可能为空） */
  detail: string;
  /** 你该做什么（后端原文；为空时由 fixHint 兜底） */
  hint: string;
  /** 外部工具原始退出码，仅诊断用 */
  exitCode: number | null;
}

const KNOWN_STATES: readonly string[] = ["ready", "unavailable", "need_login"];

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function str(source: Record<string, unknown>, key: string): string {
  const v = source[key];
  return typeof v === "string" ? v : "";
}

function toState(raw: string): ProviderState {
  return KNOWN_STATES.includes(raw) ? (raw as ProviderState) : "unknown";
}

/**
 * `ProbePublishProvider` 的 detail → 强类型视图。键名只在这里读一次。
 *
 * `auto_publish` 刻意**不读**：它是随填充包下发给**消费方**（发布插件/扩展）
 * 的字段，不是给 UI 的。UI 侧的手动发布提示必须**独立成立**
 * （见 `MANUAL_PUBLISH_NOTICE`）—— 若改成「后端说 false 才提示」，后端契约
 * 一旦漂移，UI 就跟着沉默，而那正是最不能沉默的地方。
 */
export function toProviderStatus(detail: unknown): ProviderStatusView {
  const d = asRecord(detail);
  const rawState = str(d, "state");
  return {
    state: toState(rawState),
    rawState,
    provider: str(d, "provider"),
    detail: str(d, "detail"),
    hint: str(d, "hint"),
    exitCode: typeof d.exit_code === "number" ? d.exit_code : null,
  };
}

/* ===== 判据（产品红线） ===== */

export interface ProviderBadge {
  label: string;
  /** 语气：决定样式，不决定文案 */
  tone: "success" | "warning" | "danger";
}

/**
 * 状态标。**红线一：只有 `ready` 算「可用」—— 白名单，不是黑名单。**
 *
 * 写成「非 unavailable 就算可用」是这里最容易犯的错：后端将来多一个状态值
 * （任何中间态），黑名单写法会把它当「能用」，而中间态恰恰是最不该被当成
 * 能用的那一类。未知值一律落到「不可用 + 照实显示原始值」。
 */
export function stateBadge(view: ProviderStatusView): ProviderBadge {
  switch (view.state) {
    case "ready":
      return { label: "可填充", tone: "success" };
    case "need_login":
      return { label: "需要登录", tone: "warning" };
    case "unavailable":
      return { label: "不可用", tone: "danger" };
    default:
      // 原始值照实显示：吞掉它，用户和我们都无从知道后端多加了什么
      return { label: `状态未知（${view.rawState || "空"}）`, tone: "danger" };
  }
}

/** 只有 `ready` 才允许把填充包交给发布插件。 */
export function isUsable(view: ProviderStatusView): boolean {
  return view.state === "ready";
}

/**
 * 手动发布提示。**红线二：无条件显示。**
 *
 * 这是 ADR-008 在 UI 侧的表达：不管通道可用不可用、也不管后端那个字段写了
 * 什么，这句话都成立。做成**常量**而不是函数，就是为了让它没有输入可以依赖
 * —— 没有输入，也就没有「某种情况下忘了显示」。
 */
export const MANUAL_PUBLISH_NOTICE =
  "填充只会停在预览页，最终点「发布」仍由你手动完成";

/**
 * 修法提示。**红线三：`need_login` 与 `unavailable` 的下一步完全不同。**
 *
 * 前者用户登录一下就好，后者要去安装/起 daemon。混成一句「不可用」会让用户
 * 从错误的方向开始查 —— 与后端「报错要教人怎么修」是同一条要求。
 * 后端的 `hint` 优先（它知道到底是没装、还是 daemon 没起），为空时才兜底。
 */
export function fixHint(view: ProviderStatusView): string {
  const fromBackend = view.hint.trim() || view.detail.trim();
  if (fromBackend) return fromBackend;
  switch (view.state) {
    case "ready":
      return "发布通道已就绪，无需处理";
    case "need_login":
      return "在发布插件/扩展里登录目标平台，然后重新检测";
    case "unavailable":
      return "检查发布通道是否已安装并启动，然后重新检测";
    default:
      return "状态未知：请确认前端与 worker 是同一版本，然后重新检测";
  }
}

/**
 * 诊断码行：仅在**不可用且拿到退出码**时显示。
 *
 * 就绪时不显示 —— 用户不需要知道一个正常工作的通道的退出码。
 * 但排查时它有用：用户报问题时能直接说清是哪一个码。
 */
export function diagnosticsLine(view: ProviderStatusView): string | null {
  if (view.state === "ready") return null;
  if (view.exitCode === null) return null;
  return `诊断码 ${view.exitCode}（外部工具退出码）`;
}
