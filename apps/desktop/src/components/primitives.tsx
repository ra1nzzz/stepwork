/**
 * 界面原子件。
 *
 * 这几个形态在各视图里已被手抄多遍（空态、错误条、状态徽章、加载态、
 * 行内操作组），每处的间距和措辞都略有出入 —— 用户看到的就是「同一种东西
 * 在不同页面长得不一样」。
 *
 * 收敛的判据是「已经重复出现」，不是「将来可能会用」：只抽真的抄过的形态，
 * 避免造一堆没人用的组件。
 */

import type { ReactNode } from "react";

/* -------------------------------------------------------------------------
 * 空态
 * ---------------------------------------------------------------------- */

export interface EmptyStateProps {
  title: string;
  /** 补充说明：告诉用户「下一步该做什么」，而不只是「这里没东西」 */
  hint?: ReactNode;
  action?: ReactNode;
  testId?: string;
}

export function EmptyState({ title, hint, action, testId }: EmptyStateProps) {
  return (
    <div className="empty-state" data-od-id={testId}>
      <p className="empty-title">{title}</p>
      {hint && <p className="empty-sub">{hint}</p>}
      {action && <div className="inline-actions">{action}</div>}
    </div>
  );
}

/* -------------------------------------------------------------------------
 * 提示条
 * ---------------------------------------------------------------------- */

export type NoticeTone = "info" | "success" | "warning" | "danger";

export interface NoticeBarProps {
  tone?: NoticeTone;
  children: ReactNode;
  testId?: string;
}

/**
 * 一条提示。
 *
 * ``tone`` 与语义绑定而不是与颜色绑定：调用方说「这是警告」，由这里决定
 * 长什么样 —— 否则「危险」在一处是红字、另一处是红底，用户学不会。
 */
export function NoticeBar({ tone = "info", children, testId }: NoticeBarProps) {
  const cls = tone === "danger" ? "error-text" : "panel-meta";
  return (
    <p className={cls} data-tone={tone} data-od-id={testId}>
      {children}
    </p>
  );
}

/* -------------------------------------------------------------------------
 * 状态徽章
 * ---------------------------------------------------------------------- */

export interface StatusBadgeProps {
  children: ReactNode;
  tone?: NoticeTone | "ai";
  title?: string;
}

export function StatusBadge({ children, tone = "info", title }: StatusBadgeProps) {
  return (
    <span className={`status ${tone}`} title={title}>
      {children}
    </span>
  );
}

/* -------------------------------------------------------------------------
 * 面板
 * ---------------------------------------------------------------------- */

export interface PanelProps {
  title?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  testId?: string;
}

export function Panel({ title, meta, actions, children, testId }: PanelProps) {
  return (
    <section className="panel" data-od-id={testId}>
      {(title || actions) && (
        <div className="panel-head">
          <div>
            {title && <h2 className="panel-title">{title}</h2>}
            {meta && <div className="panel-meta">{meta}</div>}
          </div>
          {actions && <div className="inline-actions">{actions}</div>}
        </div>
      )}
      <div className="panel-body">{children}</div>
    </section>
  );
}

/* -------------------------------------------------------------------------
 * 表单行
 * ---------------------------------------------------------------------- */

export interface FormRowProps {
  label: ReactNode;
  htmlFor?: string;
  /** 字段说明；写清「填错会怎样」比重复字段名有用 */
  hint?: ReactNode;
  children: ReactNode;
}

export function FormRow({ label, htmlFor, hint, children }: FormRowProps) {
  return (
    <div className="form-group">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && <p className="panel-meta">{hint}</p>}
    </div>
  );
}

/* -------------------------------------------------------------------------
 * 异步区域
 * ---------------------------------------------------------------------- */

export interface AsyncSectionProps {
  isLoading: boolean;
  error?: unknown;
  /** 数据为空时的展示；不给则空数据也走 children */
  empty?: ReactNode;
  isEmpty?: boolean;
  children: ReactNode;
}

/**
 * 加载 / 错误 / 空态的统一编排。
 *
 * 三态在 71 处被各自手写，顺序和措辞都不一致（有的先判空再判错，于是
 * 请求失败时显示「暂无数据」——用户以为真的没数据）。这里固定顺序：
 * **错误优先于空态** ——「加载失败」和「没有数据」是两件事。
 */
export function AsyncSection({
  isLoading,
  error,
  empty,
  isEmpty,
  children,
}: AsyncSectionProps) {
  if (isLoading) return <p className="feature-sub">加载中…</p>;
  if (error) {
    return (
      <NoticeBar tone="danger">
        {error instanceof Error ? error.message : String(error)}
      </NoticeBar>
    );
  }
  if (isEmpty && empty) return <>{empty}</>;
  return <>{children}</>;
}
