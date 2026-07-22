//! STEPWORK Desktop Tauri 2 主进程库。

pub mod commands;
pub mod error;
pub mod sidecar;
pub mod state;

use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::time::Duration;

use chrono::Utc;
use tauri::Manager;
use tokio::sync::mpsc;

use crate::sidecar::{spawn_sidecar, HeartbeatWatchdog, SpawnConfig};
use state::{AppState, SidecarHandle};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tracing_subscriber::fmt::init();
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(AppState::new())
        .invoke_handler(tauri::generate_handler![
            commands::health::get_worker_health,
            commands::health::restart_worker,
            commands::health::get_app_info,
            commands::dispatch::dispatch_command
        ])
        .setup(|app| {
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                run_sidecar_monitor(app_handle).await;
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// Background monitor that owns the sidecar lifecycle: spawn, heartbeat
/// supervision, and crash / self-heal restart.
async fn run_sidecar_monitor(app: tauri::AppHandle) {
    let (restart_tx, mut restart_rx) = mpsc::unbounded_channel::<()>();

    // Publish the restart sender into managed state. Clone the inner Arc out
    // in a tight scope so no borrow of `app` is held across an await.
    {
        let s = app.state::<AppState>();
        *s.restart_tx.lock().await = Some(restart_tx.clone());
    }

    let config = resolve_config();

    loop {
        match spawn_sidecar(config.clone()).await {
            Ok((child, rpc)) => {
                // Watchdog: on heartbeat timeout, ask the monitor to restart.
                let (watchdog, hb_arc) = HeartbeatWatchdog::with_default_timeout({
                    let tx = restart_tx.clone();
                    move || {
                        let _ = tx.send(());
                    }
                });

                // Route worker `runtime.heartbeat` notifications into the watchdog.
                let hb_for_handler = std::sync::Arc::clone(&hb_arc);
                rpc.set_notify_handler(std::sync::Arc::new(
                    move |method: String, _params: serde_json::Value| {
                        if method == "runtime.heartbeat" {
                            let arc = std::sync::Arc::clone(&hb_for_handler);
                            tauri::async_runtime::spawn(async move {
                                *arc.lock().await = Some(std::time::Instant::now());
                            });
                        }
                    },
                ))
                .await;

                let watchdog_handle = watchdog.start();

                let handle = SidecarHandle {
                    child,
                    rpc: std::sync::Arc::clone(&rpc),
                    watchdog: watchdog_handle,
                    session_token: config.session_token.clone(),
                    started_at: Utc::now(),
                };
                {
                    let s = app.state::<AppState>();
                    *s.sidecar.lock().await = Some(handle);
                }

                // Block until a restart is requested (heartbeat timeout or manual).
                let _ = restart_rx.recv().await;

                // Tear down + bookkeep. Clone Arcs out first so no borrow
                // of `app` is held across the awaits below.
                let (rc, lca, sidecar_arc) = {
                    let s = app.state::<AppState>();
                    (
                        s.restart_count.clone(),
                        s.last_crash_at.clone(),
                        s.sidecar.clone(),
                    )
                };
                rc.fetch_add(1, Ordering::SeqCst);
                *lca.lock().await = Some(Utc::now());
                if let Some(mut h) = sidecar_arc.lock().await.take() {
                    h.watchdog.abort();
                    let _ = h.child.kill().await;
                    let _ = h.rpc.shutdown().await;
                }

                // Small backoff before respawning.
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
            Err(e) => {
                tracing::error!("failed to spawn sidecar: {e}");
                tokio::time::sleep(Duration::from_secs(2)).await;
            }
        }
    }
}

/// Resolve the spawn configuration: prefer the repo-local venv python,
/// else fall back to `python` on PATH. Working directory is the repo root.
fn resolve_config() -> SpawnConfig {
    let repo_root = crate::sidecar::spawn::resolve_repo_root();
    let venv_python = repo_root.join(".venv").join("Scripts").join("python.exe");
    let python_path = if venv_python.exists() {
        venv_python
    } else {
        PathBuf::from("python")
    };
    SpawnConfig {
        python_path,
        cwd: Some(repo_root),
        ..Default::default()
    }
}
