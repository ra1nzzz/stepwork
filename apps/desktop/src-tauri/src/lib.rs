//! STEPWORK Desktop Tauri 2 主进程库。

pub mod commands;
pub mod error;
pub mod sidecar;
pub mod state;

use state::AppState;

/// 启动 Tauri 应用。
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tracing_subscriber::fmt::init();
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(AppState::new())
        .invoke_handler(tauri::generate_handler![
            commands::health::get_worker_health,
            commands::health::restart_worker,
            commands::health::get_app_info
        ])
        .setup(|_app| Ok(()))
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
