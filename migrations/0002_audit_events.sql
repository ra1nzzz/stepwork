-- Migration: 0002_audit_events.sql
-- Version:   0002
-- Upstream:  0001_init.sql
-- Purpose:   Create the audit_events table for unified audit logging.
--            Every Command that enters Application Services MUST emit
--            one audit record (SYSTEM_SPEC §15.5).
-- Spec:      SYSTEM_SPEC §15.5

CREATE TABLE audit_events (
    id               TEXT PRIMARY KEY,
    actor            TEXT NOT NULL,
    source_protocol  TEXT NOT NULL,
    command          TEXT NOT NULL,
    target           TEXT,
    requested_scope  TEXT,
    approval         TEXT,
    result           TEXT,
    correlation_id   TEXT,
    timestamp        TEXT NOT NULL
);
CREATE INDEX idx_audit_correlation ON audit_events(correlation_id);
CREATE INDEX idx_audit_timestamp   ON audit_events(timestamp);
