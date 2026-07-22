//! Tauri command: forward a CommandEnvelope to the worker via JSON-RPC.
//!
//! This is the single bridge that lets the React frontend issue
//! `ImportSource` / `TranscribeSource` / `AnalyzeSource` commands. The
//! worker routes `command.*` / `job.*` methods to its Command Bus.

use serde_json::{json, Value};
use tauri::State;

use crate::error::SidecarError;
use crate::state::AppState;

/// Forward a command envelope to the worker's Command Bus.
///
/// `envelope` is the full CommandEnvelope JSON matching
/// `schemas/command-envelope.schema.json`. The sidecar session token
/// is injected at the top level of `params` so the worker's auth check
/// passes, while the envelope itself is nested under `envelope` (the
/// worker reads `params["envelope"]`).
#[tauri::command]
pub async fn dispatch_command(
    state: State<'_, AppState>,
    envelope: Value,
) -> Result<Value, SidecarError> {
    let guard = state.sidecar.lock().await;
    let sidecar = guard.as_ref().ok_or_else(|| {
        SidecarError::new(
            crate::error::SidecarErrorKind::WorkerCrashed,
            "worker is not running",
        )
    })?;

    // Inject session token (top-level) + nest the envelope under "envelope".
    let mut params = json!({ "envelope": envelope });
    if let Value::Object(ref mut map) = params {
        map.insert("_session_token".into(), json!(sidecar.session_token));
    }

    sidecar.rpc.call("command.dispatch", params).await
}
