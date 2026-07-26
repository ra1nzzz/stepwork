//! STEPWORK Desktop Tauri 2 主进程库。
//!
//! W9 增补：调试模式（debug_config.rs），支持配置文件控制控制台显隐 + 日志流推送。

pub mod commands;
pub mod debug_config;
pub mod error;
pub mod sidecar;
pub mod state;

use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::sync::Arc;
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
            commands::dispatch::dispatch_command,
            commands::debug::get_debug_config,
            commands::debug::set_debug_config,
            commands::debug::get_recent_logs
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

    loop {
        let config = resolve_config(app.clone()).await;
        let session_token = config.session_token.clone();

        match spawn_sidecar(config).await {
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
                    session_token,
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
///
/// W9 增补：优先使用打包的 sidecar EXE（若存在），回退到开发模式 .venv/python
async fn resolve_config(app: tauri::AppHandle) -> SpawnConfig {
    let repo_root = crate::sidecar::spawn::resolve_repo_root();
    let venv_python = repo_root.join(".venv").join("Scripts").join("python.exe");
    let python_path = if venv_python.exists() {
        venv_python
    } else {
        PathBuf::from("python")
    };

    // 解析打包的 sidecar EXE 路径
    // Tauri 在打包模式下将 externalBin 放到可执行文件同目录
    let sidecar_path = resolve_sidecar_path(&app);

    // 读取调试模式配置
    let s = app.state::<AppState>();
    let debug = s.debug_config.get().await;
    let stderr_buf = Arc::clone(&s.stderr_buf);

    SpawnConfig {
        python_path,
        sidecar_path,
        cwd: Some(repo_root),
        debug,
        app_handle: Some(app.clone()),
        stderr_buf: Some(stderr_buf),
        ..Default::default()
    }
}

/// 当前编译目标的 Tauri sidecar target triple
/// 与 rustc -vV 的 host 一致，用于定位 sidecar 二进制
#[cfg(all(target_arch = "x86_64", target_os = "windows", target_env = "msvc"))]
const TARGET_TRIPLE: &str = "x86_64-pc-windows-msvc";

#[cfg(all(target_arch = "x86_64", target_os = "windows", target_env = "gnu"))]
const TARGET_TRIPLE: &str = "x86_64-pc-windows-gnu";

#[cfg(all(target_arch = "aarch64", target_os = "windows", target_env = "msvc"))]
const TARGET_TRIPLE: &str = "aarch64-pc-windows-msvc";

#[cfg(all(target_arch = "x86_64", target_os = "linux"))]
const TARGET_TRIPLE: &str = "x86_64-unknown-linux-gnu";

#[cfg(all(target_arch = "aarch64", target_os = "macos"))]
const TARGET_TRIPLE: &str = "aarch64-apple-darwin";

#[cfg(all(target_arch = "x86_64", target_os = "macos"))]
const TARGET_TRIPLE: &str = "x86_64-apple-darwin";

/// 解析打包的 sidecar EXE 路径
///
/// Tauri sidecar 文件名格式：`<name>-<target-triple><ext>`
/// - 开发模式：`src-tauri/binaries/stepwork-worker-<target-triple>.exe`
/// - 打包模式：`<exe_dir>/binaries/stepwork-worker-<target-triple>.exe`
fn resolve_sidecar_path(_app: &tauri::AppHandle) -> Option<PathBuf> {
    let sidecar_name = format!("stepwork-worker-{}.exe", TARGET_TRIPLE);

    // 1) 打包模式：EXE 同目录下的 binaries/
    if let Ok(exe_dir) = std::env::current_exe() {
        if let Some(parent) = exe_dir.parent() {
            let packed = parent.join("binaries").join(&sidecar_name);
            if packed.exists() {
                return Some(packed);
            }
            // 某些 Tauri 版本放在 EXE 同级而非 binaries/ 子目录
            let flat = parent.join(&sidecar_name);
            if flat.exists() {
                return Some(flat);
            }
        }
    }

    // 2) 开发模式：cwd/binaries/（cwd 是 src-tauri）
    let cwd = std::env::current_dir().unwrap_or_default();
    let dev_candidate = cwd.join("binaries").join(&sidecar_name);
    if dev_candidate.exists() {
        return Some(dev_candidate);
    }

    // 3) 开发模式备选：从 repo root 找 src-tauri/binaries/
    let repo_candidate = cwd.join("src-tauri").join("binaries").join(&sidecar_name);
    if repo_candidate.exists() {
        return Some(repo_candidate);
    }

    None
}
