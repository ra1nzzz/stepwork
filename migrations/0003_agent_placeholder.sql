-- Migration: 0003_agent_placeholder.sql
-- Version:   0003
-- Upstream:  0002_audit_events.sql
-- Purpose:   Placeholder tables for Agent Interop & Publisher domains.
--            These tables are introduced in Week 1 so that V0.2+ schema
--            evolution is additive (no destructive ALTER). Columns are
--            derived from SYSTEM_SPEC §8.2 (Agent domain) and §8.3
--            (Execution domain: PublishJob / PlatformVariant /
--            ProvenanceRecord).
--            Week 2 will populate handlers; these tables remain empty
--            until the corresponding features are enabled.
-- Spec:      SYSTEM_SPEC §8.1, §8.2, §8.3
-- Origin:    W1_MONOREPO_PLAN v1.1 Patch-A4

-- =====================================================================
-- Agent domain (SYSTEM_SPEC §8.2)
-- =====================================================================

CREATE TABLE agent_connections (
    id                   TEXT PRIMARY KEY,
    protocol             TEXT NOT NULL,
    endpoint_or_command  TEXT NOT NULL,
    local_or_remote      TEXT NOT NULL,
    trust_level          TEXT NOT NULL,
    auth_ref             TEXT,
    status               TEXT NOT NULL DEFAULT 'inactive',
    capabilities         TEXT NOT NULL DEFAULT '[]',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX idx_agent_connections_protocol ON agent_connections(protocol);
CREATE INDEX idx_agent_connections_status   ON agent_connections(status);

CREATE TABLE agent_capabilities (
    id                   TEXT PRIMARY KEY,
    agent_connection_id  TEXT NOT NULL REFERENCES agent_connections(id) ON DELETE CASCADE,
    capability_key       TEXT NOT NULL,
    capability_schema    TEXT NOT NULL DEFAULT '{}',
    enabled              INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL,
    UNIQUE (agent_connection_id, capability_key)
);
CREATE INDEX idx_agent_capabilities_connection ON agent_capabilities(agent_connection_id);

CREATE TABLE agent_sessions (
    id                   TEXT PRIMARY KEY,
    agent_connection_id  TEXT NOT NULL REFERENCES agent_connections(id) ON DELETE CASCADE,
    project_id           TEXT REFERENCES content_projects(id) ON DELETE SET NULL,
    external_session_id  TEXT,
    status               TEXT NOT NULL,
    started_at           TEXT NOT NULL,
    ended_at             TEXT
);
CREATE INDEX idx_agent_sessions_connection ON agent_sessions(agent_connection_id);
CREATE INDEX idx_agent_sessions_project    ON agent_sessions(project_id);

CREATE TABLE agent_tasks (
    id                   TEXT PRIMARY KEY,
    initiator            TEXT NOT NULL,
    target_agent_id      TEXT NOT NULL REFERENCES agent_connections(id) ON DELETE CASCADE,
    session_id           TEXT REFERENCES agent_sessions(id) ON DELETE SET NULL,
    project_id           TEXT REFERENCES content_projects(id) ON DELETE SET NULL,
    task_type            TEXT NOT NULL,
    input_artifact_ids   TEXT NOT NULL DEFAULT '[]',
    state                TEXT NOT NULL,
    progress             REAL NOT NULL DEFAULT 0.0,
    cost                 TEXT,
    timeout_at           TEXT,
    correlation_id       TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX idx_agent_tasks_agent        ON agent_tasks(target_agent_id);
CREATE INDEX idx_agent_tasks_state        ON agent_tasks(state);
CREATE INDEX idx_agent_tasks_correlation  ON agent_tasks(correlation_id);

CREATE TABLE agent_artifacts (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES content_projects(id) ON DELETE CASCADE,
    agent_task_id       TEXT REFERENCES agent_tasks(id) ON DELETE SET NULL,
    artifact_type       TEXT NOT NULL,
    schema_version      TEXT NOT NULL DEFAULT '1',
    producer_agent_id   TEXT NOT NULL,
    content_uri_or_json TEXT,
    source_refs         TEXT NOT NULL DEFAULT '[]',
    trust_level         TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    review_state        TEXT NOT NULL DEFAULT 'pending_review',
    created_at          TEXT NOT NULL
);
CREATE INDEX idx_agent_artifacts_project   ON agent_artifacts(project_id);
CREATE INDEX idx_agent_artifacts_task      ON agent_artifacts(agent_task_id);
CREATE INDEX idx_agent_artifacts_review    ON agent_artifacts(review_state);

CREATE TABLE approval_requests (
    id               TEXT PRIMARY KEY,
    actor            TEXT NOT NULL,
    action_type      TEXT NOT NULL,
    target           TEXT NOT NULL,
    requested_scope  TEXT,
    risk_summary     TEXT,
    payload          TEXT NOT NULL DEFAULT '{}',
    expires_at       TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    decision_actor   TEXT,
    decision_at      TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX idx_approval_requests_status  ON approval_requests(status);
CREATE INDEX idx_approval_requests_target  ON approval_requests(target);

-- =====================================================================
-- Publisher & Provenance domain (SYSTEM_SPEC §8.1 PlatformVariant /
-- ProvenanceRecord, §8.3 PublishJob)
-- =====================================================================

CREATE TABLE platform_variants (
    id                  TEXT PRIMARY KEY,
    content_version_id  TEXT NOT NULL REFERENCES content_versions(id) ON DELETE CASCADE,
    platform            TEXT NOT NULL,
    title               TEXT,
    body                TEXT,
    tags                TEXT NOT NULL DEFAULT '[]',
    cover_text          TEXT,
    validation_status   TEXT NOT NULL DEFAULT 'draft',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX idx_platform_variants_version  ON platform_variants(content_version_id);
CREATE INDEX idx_platform_variants_platform ON platform_variants(platform);

CREATE TABLE publish_jobs (
    id                     TEXT PRIMARY KEY,
    platform_variant_id    TEXT NOT NULL REFERENCES platform_variants(id) ON DELETE CASCADE,
    social_account_id      TEXT,
    plugin_id              TEXT,
    plugin_version         TEXT,
    state                  TEXT NOT NULL,
    approval_id            TEXT REFERENCES approval_requests(id) ON DELETE SET NULL,
    evidence_artifact_ids  TEXT NOT NULL DEFAULT '[]',
    remote_content_id      TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE INDEX idx_publish_jobs_variant ON publish_jobs(platform_variant_id);
CREATE INDEX idx_publish_jobs_state   ON publish_jobs(state);

CREATE TABLE provenance_records (
    id                  TEXT PRIMARY KEY,
    subject_type        TEXT NOT NULL,
    subject_id          TEXT NOT NULL,
    source_ids          TEXT NOT NULL DEFAULT '[]',
    model_calls         TEXT NOT NULL DEFAULT '[]',
    agent_tasks         TEXT NOT NULL DEFAULT '[]',
    plugin_executions   TEXT NOT NULL DEFAULT '[]',
    user_edits          TEXT NOT NULL DEFAULT '[]',
    ai_label_state      TEXT NOT NULL DEFAULT 'unknown',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (subject_type, subject_id)
);
CREATE INDEX idx_provenance_subject ON provenance_records(subject_type, subject_id);
