-- Migration: 0001_init.sql
-- Version:   0001
-- Upstream:  (none — initial schema)
-- Purpose:   Create the five core tables for STEPWORK V0.1:
--            workspaces / content_projects / source_assets / jobs / content_versions
-- Spec:      SYSTEM_SPEC §8.1, §8.3, §9.1
-- Encoding:  UTF-8. SQL keywords UPPERCASE. 4-space indent.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE workspaces (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    root_path       TEXT NOT NULL,
    settings        TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    archived_at     TEXT
);

CREATE TABLE content_projects (
    id                          TEXT PRIMARY KEY,
    workspace_id                TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title                       TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'active',
    brand_profile_id            TEXT,
    current_content_version_id  TEXT,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);
CREATE INDEX idx_content_projects_workspace ON content_projects(workspace_id);

CREATE TABLE source_assets (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES content_projects(id) ON DELETE CASCADE,
    kind                TEXT NOT NULL,
    local_uri           TEXT NOT NULL,
    original_uri        TEXT,
    content_hash        TEXT NOT NULL,
    rights_declaration  TEXT,
    metadata            TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    UNIQUE (project_id, content_hash)
);
CREATE INDEX idx_source_assets_project ON source_assets(project_id);
CREATE INDEX idx_source_assets_hash    ON source_assets(content_hash);

CREATE TABLE jobs (
    id                   TEXT PRIMARY KEY,
    job_type             TEXT NOT NULL,
    state                TEXT NOT NULL,
    stage                TEXT,
    payload              TEXT NOT NULL DEFAULT '{}',
    progress             REAL    NOT NULL DEFAULT 0.0,
    attempt_count        INTEGER NOT NULL DEFAULT 0,
    max_attempts         INTEGER NOT NULL DEFAULT 3,
    lease_owner          TEXT,
    lease_expires_at     TEXT,
    heartbeat_at         TEXT,
    error_code           TEXT,
    result_artifact_ids  TEXT NOT NULL DEFAULT '[]',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX idx_jobs_state_lease ON jobs(state, lease_expires_at);
CREATE INDEX idx_jobs_type        ON jobs(job_type);

CREATE TABLE content_versions (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES content_projects(id) ON DELETE CASCADE,
    parent_version_id   TEXT REFERENCES content_versions(id) ON DELETE SET NULL,
    content_type        TEXT NOT NULL,
    content             TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    producer            TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL
);
CREATE INDEX idx_content_versions_project ON content_versions(project_id);
