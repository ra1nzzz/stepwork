-- 0011: 命令级本地指标
--
-- 18k 行后端此前只有 38 处零散日志、0 个指标 —— 用户说「渲染很慢」，我们既
-- 不知道慢在哪一步，也不知道是不是只有他慢。
--
-- 刻意**不引 Prometheus / OpenTelemetry**：这是本地优先的桌面应用，为看几个
-- 计数拉起一套监控栈，用户既装不动也不会去看。指标直接进 SQLite，随
-- ExportDiagnosticsBundle 一并带走。
--
-- 保留策略随既有的 retention 清扫（RunCleanup）—— 指标表不设上限会在长期
-- 使用后变成库里最大的一张表。

CREATE TABLE command_metrics (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    command_type   TEXT NOT NULL,
    ok             INTEGER NOT NULL,
    duration_ms    REAL NOT NULL,
    -- 只存错误码前缀（如 FORBIDDEN_ACTOR），不存完整消息：消息里可能带
    -- 用户内容，而聚合分析只需要分类
    error_code     TEXT,
    -- 贯穿 UI → Rust → worker 的调用链 id，诊断时用它串起一次完整操作
    correlation_id TEXT,
    recorded_at    TEXT NOT NULL
);

CREATE INDEX idx_command_metrics_type ON command_metrics(command_type);
CREATE INDEX idx_command_metrics_time ON command_metrics(recorded_at);
CREATE INDEX idx_command_metrics_cid  ON command_metrics(correlation_id);
