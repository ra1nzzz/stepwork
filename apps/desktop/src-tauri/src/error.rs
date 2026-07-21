//! Error types for STEPWORK sidecar communication.
//!
//! Aligns with SYSTEM_SPEC §16 Error Envelope.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use uuid::Uuid;

/// Category of sidecar errors for UX differentiation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SidecarErrorKind {
    /// Python interpreter not found.
    PythonMissing,
    /// Failed to spawn the worker process.
    SpawnFailed,
    /// Worker did not send `runtime.ready` within timeout.
    HandshakeTimeout,
    /// RPC protocol violation (malformed frame, version mismatch).
    RpcProtocolError,
    /// Worker process crashed unexpectedly.
    WorkerCrashed,
    /// Frame exceeds MAX_FRAME_SIZE.
    FrameTooLarge,
    /// JSON parse error.
    ParseError,
    /// Graceful shutdown in progress.
    Shutdown,
    /// Unclassified error.
    Unknown,
}

/// Structured sidecar error matching SYSTEM_SPEC §16 Error Envelope.
#[derive(Debug, Clone, Serialize, Deserialize, thiserror::Error)]
#[error("[{code}] {message}")]
pub struct SidecarError {
    /// Error category for UX branching.
    pub kind: SidecarErrorKind,
    /// Stable error code (e.g. "SPAWN_FAILED").
    pub code: String,
    /// Human-readable message.
    pub message: String,
    /// Whether the caller may retry.
    pub retryable: bool,
    /// Additional structured details.
    #[serde(default)]
    pub details: Value,
    /// Correlation ID for audit tracing.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub correlation_id: Option<Uuid>,
}

impl SidecarError {
    /// Create a new error with the given kind.
    pub fn new(kind: SidecarErrorKind, message: impl Into<String>) -> Self {
        let code = format!("{:?}", kind).to_uppercase();
        Self {
            kind,
            code,
            message: message.into(),
            retryable: matches!(
                kind,
                SidecarErrorKind::HandshakeTimeout | SidecarErrorKind::WorkerCrashed
            ),
            details: Value::Null,
            correlation_id: None,
        }
    }

    /// Attach details payload.
    pub fn with_details(mut self, details: Value) -> Self {
        self.details = details;
        self
    }

    /// Attach correlation ID.
    pub fn with_correlation_id(mut self, id: Uuid) -> Self {
        self.correlation_id = Some(id);
        self
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn serializes_to_error_envelope() {
        let err = SidecarError::new(SidecarErrorKind::SpawnFailed, "failed to spawn python")
            .with_details(serde_json::json!({"stderr": "module not found"}));
        let json = serde_json::to_value(&err).expect("serialize");
        assert_eq!(json["kind"], "spawn_failed");
        assert_eq!(json["code"], "SPAWNFAILED");
        assert!(json["message"].as_str().expect("msg").contains("python"));
        assert_eq!(json["retryable"], false);
        assert!(json["details"]["stderr"].as_str().is_some());
    }

    #[test]
    fn retryable_only_for_transient() {
        let timeout = SidecarError::new(SidecarErrorKind::HandshakeTimeout, "timeout");
        assert!(timeout.retryable);
        let missing = SidecarError::new(SidecarErrorKind::PythonMissing, "no python");
        assert!(!missing.retryable);
    }
}
