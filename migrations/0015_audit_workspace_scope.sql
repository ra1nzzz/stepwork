-- Migration: 0015_audit_workspace_scope.sql
-- Version:   0015
-- Upstream:  0014_hotspot_items.sql
-- Purpose:   给 ``audit_events`` 与 ``approval_requests`` 加 ``workspace_id`` 列，
--            修「ListAuditEvents / ListApprovalRequests 无 workspace 过滤 +
--            audit_events 表根本没这列」的跨工作区数据泄露（review P1 安全项）。
--
--            此前：
--              ① audit_events 只有 target 列（存的是 projectId 或 workspaceId，
--                 混着放），ListAuditEvents 拿不到 workspace 维度的信息；
--              ② ListAuditEvents 在 bus 的 _AGENT_ALLOWED_COMMANDS 里 —— 外部
--                 MCP Agent 可以读到全库审计事件与审批请求，跨工作区信息泄露；
--              ③ approval_requests 有 requested_scope 但没有 workspace_id，
--                 ListApprovalRequests 同样无过滤。
--
--            本迁移：
--              - 两张表各加 ``workspace_id TEXT``（**可空**，向后兼容旧数据：
--                旧行留 NULL 表示"归属未知"，读侧默认过滤掉，宁可少看不多看）；
--              - 索引建在 workspace_id 上，让常见过滤走索引而不是全表扫；
--              - 写侧（audit.record_event / approvals.create_request）从下一
--                次开始带上 workspace_id；老数据不追补（追补需要一次
--                target→workspace 的启发式映射，风险大于收益）。
--
-- Spec:      yt-dev-review 第三轮评审报告 dimension A P1 #4 / 盲区 2 安全性
-- Origin:    2026-09-14 第四轮评审遗留 → 本轮落地

ALTER TABLE audit_events ADD COLUMN workspace_id TEXT;
CREATE INDEX IF NOT EXISTS idx_audit_workspace
    ON audit_events(workspace_id, timestamp);

ALTER TABLE approval_requests ADD COLUMN workspace_id TEXT;
CREATE INDEX IF NOT EXISTS idx_approval_workspace
    ON approval_requests(workspace_id, created_at);
