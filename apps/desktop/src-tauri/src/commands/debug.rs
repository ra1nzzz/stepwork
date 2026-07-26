//! Tauri commands for debug mode (W9 增补)
//!
//! - get_debug_config / set_debug_config: 读写 $STEPWORK_HOME/debug.json
//! - get_recent_logs: 返回 worker stderr 环形缓冲最近内容

use serde_json::Value;
use tauri::State;

use crate::debug_config::DebugConfig;
use crate::state::AppState;

/// 读取调试模式配置
#[tauri::command]
pub async fn get_debug_config(state: State<'_, AppState>) -> Result<Value, String> {
    let config = state.debug_config.get().await;
    serde_json::to_value(config).map_err(|e| e.to_string())
}

/// 更新调试模式配置（持久化到 debug.json）
///
/// 注意：console / python_window 的变更需要重启 worker 才完全生效
/// （console 日志流推送在 spawn 时决定；python_window 在进程创建时决定）
#[tauri::command]
pub async fn set_debug_config(
    state: State<'_, AppState>,
    config: DebugConfig,
) -> Result<Value, String> {
    state
        .debug_config
        .set(config.clone())
        .await
        .map_err(|e| e.to_string())?;
    serde_json::to_value(config).map_err(|e| e.to_string())
}

/// 返回 worker stderr 环形缓冲的最近内容（4KB）
#[tauri::command]
pub async fn get_recent_logs(state: State<'_, AppState>) -> Result<Value, String> {
    let buf = state.stderr_buf.lock().await;
    Ok(serde_json::json!({
        "logs": buf.clone(),
        "size": buf.len(),
    }))
}
