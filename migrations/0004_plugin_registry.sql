-- Migration: 0004_plugin_registry.sql
-- Version:   0004
-- Upstream:  0003_agent_placeholder.sql
-- Purpose:   Create the installed_plugins table for the plugin registry (W8 L.29).
--            Stores plugin manifest JSON + enable/disable state + load status.
--            Enable/Disable only flips DB state; actual subprocess spawn is V0.2
--            (ADR-009 Plugin Isolated Process).
-- Spec:      ADR-009; SYSTEM_SPEC §12

CREATE TABLE IF NOT EXISTS installed_plugins (
    id              TEXT PRIMARY KEY,
    manifest_json   TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 0,
    installed_at    TEXT NOT NULL,
    last_loaded_at  TEXT,
    status          TEXT NOT NULL DEFAULT 'registered',
    error_message   TEXT
);
