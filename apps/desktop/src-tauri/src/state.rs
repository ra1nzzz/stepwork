//! Application state managed by Tauri.

use chrono::{DateTime, Utc};
use std::sync::atomic::AtomicU32;
use std::sync::Arc;
use tokio::process::Child;
use tokio::sync::mpsc;
use tokio::sync::Mutex;
use tokio::task::JoinHandle;

use crate::sidecar::RpcClient;

/// Global application state.
pub struct AppState {
    /// Current sidecar handle (None when worker is down).
    pub sidecar: Arc<Mutex<Option<SidecarHandle>>>,
    /// Number of times worker has been (re)started.
    pub restart_count: Arc<AtomicU32>,
    /// Timestamp of last worker crash / restart.
    pub last_crash_at: Arc<Mutex<Option<DateTime<Utc>>>>,
    /// Sender used to request a sidecar restart (consumed by the monitor loop).
    pub restart_tx: Arc<Mutex<Option<mpsc::UnboundedSender<()>>>>,
}

impl AppState {
    /// Create a fresh AppState.
    pub fn new() -> Self {
        Self {
            sidecar: Arc::new(Mutex::new(None)),
            restart_count: Arc::new(AtomicU32::new(0)),
            last_crash_at: Arc::new(Mutex::new(None)),
            restart_tx: Arc::new(Mutex::new(None)),
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
