-- Migration: 0009_idempotency.sql
-- Version:   0009
-- Upstream:  0008_brand_reference_scripts.sql
-- Purpose:   PRD §13 验收指标「重复任务幂等阻止重复输出」。
--
--            command-envelope.schema.json 早就有 idempotencyKey 字段并写着
--            「Side-effecting commands SHOULD provide one」，但全仓**从未
--            消费**它：重复提交同一命令会重复产出内容版本、重复计费。
--            此表缓存已成功命令的结果，重放时直接返回而不再执行。
-- Spec:      PRD §13；SYSTEM_SPEC §7

CREATE TABLE IF NOT EXISTS command_idempotency (
    -- 作用域含 commandType：不同命令即便用了同一个 key 也互不干扰
    workspace_id    TEXT NOT NULL,
    command_type    TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    -- 缓存的 CommandResult（JSON），重放时原样返回
    result_json     TEXT NOT NULL,
    command_id      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (workspace_id, command_type, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_idempotency_created
    ON command_idempotency(created_at);
