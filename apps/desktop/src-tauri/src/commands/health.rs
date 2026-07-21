//! Tauri commands for health and lifecycle.

use serde_json::{json, Value};
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

/// Restart the worker sidecar.
#[tauri::command]
pub async fn restart_worker(state: State<'_, AppState>) -> Result<(), SidecarError> {
    let mut guard = state.sidecar.lock().await;

    if let Some(handle) = guard.take() {
        handle.rpc.shutdown().await;
        handle.watchdog.abort();
        let mut child = handle.child;
        let _ = child.kill().await;
    }

    state
        .restart_count
        .fetch_add(1, std::sync::atomic::Ordering::SeqCst);

    // The actual re-spawn is performed by the setup/background monitor;
    // this command only tears down and signals.
    *state.last_crash_at.lock().await = Some(chrono::Utc::now());
    Ok(())
}

/// Get application metadata.
#[tauri::command]
pub async fn get_app_info(state: State<'_, AppState>) -> Result<Value, SidecarError> {
    let restart_count = state
        .restart_count
        .load(std::sync::atomic::Ordering::SeqCst);
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
