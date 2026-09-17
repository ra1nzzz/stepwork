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

impl SidecarErrorKind {
    /// Stable SCREAMING_SNAKE_CASE code, guaranteed to be the uppercase of
    /// the same token that `Serialize` emits for `kind` (which uses
    /// `rename_all = "snake_case"`).
    ///
    /// Why: the old `format!("{kind:?}").to_uppercase()` produced
    /// `"SPAWNFAILED"` from the Debug of `SpawnFailed` — it dropped the
    /// underscore, so `code` and the serialized `kind` (`"spawn_failed"`)
    /// disagreed. Frontend / CLI branches that key off `code` would mis-spell
    /// it. Deriving the code from the serde snake_case form keeps the two
    /// representations in lockstep.
    pub fn as_code(&self) -> &'static str {
        match self {
            SidecarErrorKind::PythonMissing => "PYTHON_MISSING",
            SidecarErrorKind::SpawnFailed => "SPAWN_FAILED",
            SidecarErrorKind::HandshakeTimeout => "HANDSHAKE_TIMEOUT",
            SidecarErrorKind::RpcProtocolError => "RPC_PROTOCOL_ERROR",
            SidecarErrorKind::WorkerCrashed => "WORKER_CRASHED",
            SidecarErrorKind::FrameTooLarge => "FRAME_TOO_LARGE",
            SidecarErrorKind::ParseError => "PARSE_ERROR",
            SidecarErrorKind::Shutdown => "SHUTDOWN",
            SidecarErrorKind::Unknown => "UNKNOWN",
        }
    }
}

impl SidecarError {
    /// Create a new error with the given kind.
    pub fn new(kind: SidecarErrorKind, message: impl Into<String>) -> Self {
        let code = kind.as_code().to_string();
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
        assert_eq!(json["code"], "SPAWN_FAILED");
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

    /// P1-R5 锁：`code` 必须是序列化 `kind` 的大写形式，两者不能再漂移。
    /// 覆盖所有变体，防止将来给 `SidecarErrorKind` 加新变体时
    /// `as_code()` 漏配（编译器已强制穷举）或有人把 code 改回
    /// `{:?}` 拼接（那条路径不会随 serde 的 snake_case 更新）。
    #[test]
    fn code_is_uppercase_of_serialized_kind_for_all_variants() {
        use SidecarErrorKind::*;
        let all = [
            PythonMissing,
            SpawnFailed,
            HandshakeTimeout,
            RpcProtocolError,
            WorkerCrashed,
            FrameTooLarge,
            ParseError,
            Shutdown,
            Unknown,
        ];
        for kind in all {
            let serialized = serde_json::to_value(kind).expect("kind serializes");
            let kind_snake = serialized.as_str().expect("kind is a string");
            let expected_code = kind_snake.to_uppercase();
            assert_eq!(
                kind.as_code(),
                expected_code.as_str(),
                "code/kind 漂移：kind 序列化成 {kind_snake:?} 但 code 是 {:?}",
                kind.as_code()
            );
            let err = SidecarError::new(kind, "x");
            assert_eq!(err.code, expected_code);
        }
    }
}
