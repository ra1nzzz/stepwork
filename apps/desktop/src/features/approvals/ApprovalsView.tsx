/**
 * 审批中心（PRD-AGT-008 / §9.2）。
 *
 * 统一处理 Agent、插件与发布类审批。列表来自 ListApprovalRequests，
 * 每条展示 §9.2 要求的七要素：请求者 / 动作 / 目标 / 将涉及的数据 /
 * 费用或风险 / 有效期 / 授权范围。
 *
 * 批准**不等于**自动执行 —— 后端明确返回 auto_executed=false，仍需用户
 * 在对应页面自行发起（PRD §10.6 默认不过度自动化）。
 */

import { useCallback, useEffect, useState } from "react";
import { buildEnvelope, dispatchCommand, getWorkspaceId } from "@/lib/tauri";
import type { ApprovalRequest } from "@/lib/types";

const STATUS_LABELS: Record<string, string> = {
  pending: "待审批",
  approved: "已批准",
  rejected: "已拒绝",
  expired: "已过期",
  consumed: "已使用",
};

function statusClass(status: string): string {
  if (status === "approved") return "success";
  if (status === "rejected") return "danger";
  if (status === "pending") return "warning";
  return "";
}

function scopeLabel(scope: string | null): string {
  if (scope === "once") return "一次性授权";
  if (scope === "persistent") return "持久授权";
  return scope ?? "未声明";
}

export function ApprovalsView() {
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [isBusy, setIsBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [decidingId, setDecidingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsBusy(true);
    setError(null);
    try {
      const env = buildEnvelope(
        "ListApprovalRequests",
        getWorkspaceId(),
        null,
        {},
      );
      const res = await dispatchCommand(env);
      if (!res.ok) {
        setError(res.error ?? "读取审批列表失败");
        return;
      }
      const detail = (res.detail ?? {}) as { approvals?: ApprovalRequest[] };
      setApprovals(detail.approvals ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function decide(id: string, decision: "approve" | "reject") {
    setDecidingId(id);
    setError(null);
    try {
      const env = buildEnvelope(
        "DecideApprovalRequest",
        getWorkspaceId(),
        null,
        { approvalId: id, decision },
      );
      const res = await dispatchCommand(env);
      if (!res.ok) setError(res.error ?? "审批失败");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDecidingId(null);
    }
  }

  const pending = approvals.filter((a) => a.status === "pending");

  return (
    <section className="feature-view" data-od-id="approvals-view">
      <div className="page-head">
        <div>
          <p className="eyebrow">APPROVAL CENTER</p>
          <h1>审批中心</h1>
          <p className="page-subtitle">
            外部 Agent 与插件的高风险请求会降级为准备任务，在此统一确认。
            批准后仍需你自行发起，系统不会自动执行。
          </p>
        </div>
        <span className={`status ${pending.length > 0 ? "warning" : "success"}`}>
          {pending.length > 0 ? `${pending.length} 条待审批` : "无待办"}
        </span>
      </div>

      {error && (
        <p className="error-text" data-od-id="approvals-error">
          {error}
        </p>
      )}

      {isBusy && <p className="feature-sub">加载中…</p>}

      {!isBusy && approvals.length === 0 && !error && (
        <div className="empty-state">
          <p className="empty-title">暂无审批请求。</p>
          <p className="empty-sub">
            当外部 Agent 请求发布、删除或安装插件等操作时，会出现在这里。
          </p>
        </div>
      )}

      {approvals.length > 0 && (
        <ul className="approval-list">
          {approvals.map((a) => (
            <li key={a.id} className="panel" data-od-id={`approval-${a.id}`}>
              <div className="panel-head">
                <div>
                  <h2 className="panel-title">{a.action_type}</h2>
                  <div className="panel-meta">
                    请求者 {a.actor} · 目标 {a.target}
                  </div>
                </div>
                <span className={`status ${statusClass(a.status)}`}>
                  {STATUS_LABELS[a.status] ?? a.status}
                </span>
              </div>
              <div className="panel-body">
                {/* §9.2 七要素 */}
                <dl className="provenance">
                  <div className="provenance-row">
                    <dt>授权范围</dt>
                    <dd>{scopeLabel(a.requested_scope)}</dd>
                  </div>
                  <div className="provenance-row">
                    <dt>有效期至</dt>
                    <dd>
                      {a.expires_at
                        ? new Date(a.expires_at).toLocaleString()
                        : "未设置"}
                    </dd>
                  </div>
                  {a.risk_summary && (
                    <div className="provenance-row">
                      <dt>费用/风险</dt>
                      <dd>{a.risk_summary}</dd>
                    </div>
                  )}
                  <div className="provenance-row">
                    <dt>将涉及的数据</dt>
                    <dd className="mono" style={{ fontSize: 11 }}>
                      {JSON.stringify(a.payload)}
                    </dd>
                  </div>
                </dl>

                {a.status === "pending" ? (
                  <div className="inline-actions">
                    <button
                      type="button"
                      className="btn small primary"
                      disabled={decidingId === a.id}
                      onClick={() => void decide(a.id, "approve")}
                    >
                      批准
                    </button>
                    <button
                      type="button"
                      className="btn small ghost"
                      disabled={decidingId === a.id}
                      onClick={() => void decide(a.id, "reject")}
                    >
                      拒绝
                    </button>
                  </div>
                ) : (
                  <p className="panel-meta">
                    {a.decision_actor
                      ? `由 ${a.decision_actor} 于 ${
                          a.decision_at
                            ? new Date(a.decision_at).toLocaleString()
                            : "未知时间"
                        } 处理`
                      : "无需处理"}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
