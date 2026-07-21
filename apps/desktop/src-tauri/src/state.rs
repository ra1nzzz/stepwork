//! Application state managed by Tauri.

use std::sync::atomic::AtomicU32;
use std::sync::Arc;
use chrono::{DateTime, Utc};
use tokio::process::Child;
use tokio::sync::Mutex;
use tokio::task::JoinHandle;

use crate::sidecar::RpcClient;

/// Global application state.
pub struct AppState {
    /// Current sidecar handle (None when worker is down).
    pub sidecar: Arc<Mutex<Option<SidecarHandle>>>,
    /// Number of times worker has been restarted.
    pub restart_count: Arc<AtomicU32>,
    /// Timestamp of last worker crash.
    pub last_crash_at: Arc<Mutex<Option<DateTime<Utc>>>>,
}

impl AppState {
    /// Create a fresh AppState.
    pub fn new() -> Self {
        Self {
            sidecar: Arc::new(Mutex::new(None)),
            restart_count: Arc::new(AtomicU32::new(0)),
            last_crash_at: Arc::new(Mutex::new(None)),
        }
    }
}

impl Default for AppState {
    fn default() -> Self {
        Self::new()
    }
}

/// Handle to a running sidecar worker.
pub struct SidecarHandle {
    /// Child process.
    pub child: Child,
    /// RPC client for JSON-RPC over stdio.
    pub rpc: Arc<RpcClient>,
    /// Watchdog task monitoring heartbeat.
    pub watchdog: JoinHandle<()>,
    /// Session token for this sidecar instance.
    pub session_token: String,
    /// When the sidecar was started.
    pub started_at: DateTime<Utc>,
}
