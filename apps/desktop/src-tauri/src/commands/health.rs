//! Tauri commands for health and lifecycle.

use serde_json::{json, Value};
use std::sync::atomic::Ordering;
use tauri::State;

use crate::error::SidecarError;
use crate::state::AppState;

/// Fetch the current worker health status.
#[tauri::command]
pub async fn get_worker_health(state: State<'_, AppState>) -> Result<Value, SidecarError> {
    let guard = state.sidecar.lock().await;
    let sidecar = guard.as_ref().ok_or_else(|| {
        SidecarError::new(
            crate::error::SidecarErrorKind::WorkerCrashed,
            "worker is not running",
        )
    })?;
    sidecar.rpc.call("runtime.health_check", json!({})).await
}

/// Request a sidecar restart.
///
/// Signals the background monitor loop (via the shared `restart_tx`) which
/// tears down the current worker and re-spawns it. Crash bookkeeping
/// (restart count / last-crash timestamp) is updated by the monitor.
#[tauri::command]
pub async fn restart_worker(state: State<'_, AppState>) -> Result<(), SidecarError> {
    let guard = state.restart_tx.lock().await;
    match guard.as_ref() {
        Some(tx) => {
            let _ = tx.send(());
            Ok(())
        }
        None => Err(SidecarError::new(
            crate::error::SidecarErrorKind::WorkerCrashed,
            "sidecar monitor is not initialized",
        )),
    }
}

/// Get application metadata.
#[tauri::command]
pub async fn get_app_info(state: State<'_, AppState>) -> Result<Value, SidecarError> {
    let restart_count = state.restart_count.load(Ordering::SeqCst);
    let last_crash_at = *state.last_crash_at.lock().await;

    Ok(json!({
        "version": env!("CARGO_PKG_VERSION"),
        "platform": std::env::consts::OS,
        "stepwork_home": dirs_stepwork_home(),
        "restart_count": restart_count,
        "last_crash_at": last_crash_at.map(|t| t.to_rfc3339()),
    }))
}

/// Resolve STEPWORK_HOME directory.
fn dirs_stepwork_home() -> String {
    std::env::var("STEPWORK_HOME").unwrap_or_else(|_| {
        let mut path = std::env::temp_dir();
        path.push("stepwork-home");
        path.to_string_lossy().into_owned()
    })
}
