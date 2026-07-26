//! Tauri command: forward a CommandEnvelope to the worker via JSON-RPC.
//!
//! This is the single bridge that lets the React frontend issue
//! `ImportSource` / `TranscribeSource` / `AnalyzeSource` commands. The
//! worker routes `command.*` / `job.*` methods to its Command Bus.

use serde_json::{json, Value};
use std::sync::Arc;
use std::time::Duration;
use tauri::State;

use crate::error::SidecarError;
use crate::state::AppState;

/// `command.dispatch` 超时：30 分钟。
///
/// 转写 / 渲染等长任务同步等待完成回执；真正的取消依赖 CancelJob
/// 命令，而非 RPC 超时（契约「RPC 超时与并发」）。
const DISPATCH_TIMEOUT: Duration = Duration::from_secs(30 * 60);

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
    // 在锁内只 clone 出 Arc<RpcClient> 与 session_token，随即释放锁：
    // 绝不能在 await RPC 期间持有 state.sidecar 锁，否则健康检查 /
    // 重启监控会被长任务阻塞长达 30 分钟。
    let (rpc, session_token) = {
        let guard = state.sidecar.lock().await;
        let sidecar = guard.as_ref().ok_or_else(|| {
            SidecarError::new(
                crate::error::SidecarErrorKind::WorkerCrashed,
                "worker is not running",
            )
        })?;
        (Arc::clone(&sidecar.rpc), sidecar.session_token.clone())
    };

    // Inject session token (top-level) + nest the envelope under "envelope".
    let mut params = json!({ "envelope": envelope });
    if let Value::Object(ref mut map) = params {
        map.insert("_session_token".into(), json!(session_token));
    }

    rpc.call_with_timeout("command.dispatch", params, DISPATCH_TIMEOUT)
        .await
}
