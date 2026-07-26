//! Sidecar process spawning and handshake.
//!
//! W9 增补：调试模式支持
//! - `console=true`：stderr 行实时转发为 Tauri event `worker://log`
//! - `python_window=false`：Windows 下设置 `CREATE_NO_WINDOW` 隐藏子进程控制台

use serde_json::json;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;
use tokio::time::timeout;

use crate::debug_config::DebugConfig;
use crate::error::{SidecarError, SidecarErrorKind};
use crate::sidecar::rpc_client::RpcClient;

/// Configuration for spawning a sidecar worker.
#[derive(Clone)]
pub struct SpawnConfig {
    /// Path to Python interpreter (开发模式：.venv/Scripts/python.exe)
    pub python_path: PathBuf,
    /// 打包的 sidecar EXE 路径（打包模式：binaries/stepwork-worker-<target>.exe）
    pub sidecar_path: Option<PathBuf>,
    /// Python module to run (default "worker.runtime")。
    /// 打包模式下此字段被忽略（sidecar EXE 直接执行入口）
    pub worker_module: String,
    /// Session token for this sidecar instance.
    pub session_token: String,
    /// Timeout waiting for the worker to become ready.
    pub ready_timeout: Duration,
    /// Working directory the sidecar is spawned in (defaults to repo root).
    pub cwd: Option<PathBuf>,
    /// 调试模式配置
    pub debug: DebugConfig,
    /// Tauri AppHandle，用于 emit 日志事件（None 时不推送）
    pub app_handle: Option<AppHandle>,
    /// 共享的 stderr 环形缓冲（诊断用，None 时使用本地临时缓冲）
    pub stderr_buf: Option<Arc<Mutex<String>>>,
}

impl Default for SpawnConfig {
    fn default() -> Self {
        Self {
            python_path: PathBuf::from("python"),
            sidecar_path: None,
            worker_module: "worker.runtime".to_string(),
            session_token: uuid::Uuid::new_v4().to_string(),
            ready_timeout: Duration::from_secs(10),
            cwd: Some(resolve_repo_root()),
            debug: DebugConfig::default(),
            app_handle: None,
            stderr_buf: None,
        }
    }
}

/// Walk up from the current directory to locate the repository root,
/// identified by a `worker/runtime/__init__.py` marker or a `.git` directory.
pub fn resolve_repo_root() -> PathBuf {
    let mut dir = std::env::current_dir().unwrap_or_default();
    loop {
        if dir
            .join("worker")
            .join("runtime")
            .join("__init__.py")
            .exists()
            || dir.join(".git").exists()
        {
            return dir;
        }
        if !dir.pop() {
            break;
        }
    }
    std::env::current_dir().unwrap_or_default()
}

/// Spawn a Python worker sidecar and perform the ready handshake.
///
/// Returns the child process and an `Arc<RpcClient>` ready for use.
pub async fn spawn_sidecar(config: SpawnConfig) -> Result<(Child, Arc<RpcClient>), SidecarError> {
    let debug = config.debug.clone();
    let app_handle = config.app_handle.clone();
    // 使用共享 stderr 缓冲（来自 AppState），或回退到本地临时缓冲
    let stderr_buf = config.stderr_buf.clone().unwrap_or_else(|| Arc::new(Mutex::new(String::new())));

    let cwd = config
        .cwd
        .clone()
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());

    // 优先使用打包的 sidecar EXE；回退到 .venv/python + -m worker.runtime
    let mut cmd = if let Some(sidecar) = &config.sidecar_path {
        if sidecar.exists() {
            let mut c = Command::new(sidecar);
            c.env("STEPWORK_SESSION_TOKEN", &config.session_token)
                .env("STEPWORK_LOG_LEVEL", &debug.log_level)
                .current_dir(&cwd)
                .stdin(std::process::Stdio::piped())
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped());
            c
        } else {
            return Err(SidecarError::new(
                SidecarErrorKind::PythonMissing,
                format!("sidecar binary not found at {}", sidecar.display()),
            ));
        }
    } else {
        // 开发模式：python -m worker.runtime
        if config.python_path.is_absolute() && !config.python_path.exists() {
            return Err(SidecarError::new(
                SidecarErrorKind::PythonMissing,
                format!("python not found at {}", config.python_path.display()),
            ));
        }
        let mut c = Command::new(&config.python_path);
        c.args(["-m", &config.worker_module])
            .env("STEPWORK_SESSION_TOKEN", &config.session_token)
            .env("STEPWORK_LOG_LEVEL", &debug.log_level)
            .current_dir(&cwd)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());
        c
    };

    // Windows 下按配置决定是否隐藏 Python 子进程控制台窗口
    #[cfg(target_os = "windows")]
    if !debug.python_window {
        // CREATE_NO_WINDOW = 0x08000000
        // tokio::process::Command 在 Windows 上原生提供 creation_flags 方法
        cmd.creation_flags(0x08000000);
    }

    let mut child = cmd.spawn().map_err(|e| {
        SidecarError::new(
            SidecarErrorKind::SpawnFailed,
            format!("failed to spawn python: {e}"),
        )
    })?;

    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| SidecarError::new(SidecarErrorKind::SpawnFailed, "stdin unavailable"))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| SidecarError::new(SidecarErrorKind::SpawnFailed, "stdout unavailable"))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| SidecarError::new(SidecarErrorKind::SpawnFailed, "stderr unavailable"))?;

    // Capture stderr into a ring buffer for diagnostics + 转发到前端（若 console 开启）
    let stderr_clone = Arc::clone(&stderr_buf);
    let console_enabled = debug.console;
    let emit_handle = app_handle.clone();
    tokio::spawn(async move {
        let mut reader = BufReader::new(stderr).lines();
        while let Ok(Some(line)) = reader.next_line().await {
            // 1) 写入 ring buffer（诊断用，4KB 上限）
            {
                let mut buf = stderr_clone.lock().await;
                buf.push_str(&line);
                buf.push('\n');
                if buf.len() > 4096 {
                    let drain_to = buf.len() - 4096;
                    buf.drain(..drain_to);
                }
            }
            // 2) 转发到前端 DebugConsole（若 console 开启）
            if console_enabled {
                if let Some(handle) = &emit_handle {
                    let _ = handle.emit(
                        "worker://log",
                        json!({
                            "line": line,
                            "ts": chrono::Utc::now().to_rfc3339(),
                            "source": "worker.stderr",
                        }),
                    );
                }
            }
        }
    });

    let rpc = RpcClient::new(stdin, stdout);

    // Wait for worker to become ready by polling health_check.
    let ready_future = async {
        for _ in 0..20 {
            match rpc.call("runtime.health_check", json!({})).await {
                Ok(_) => return Ok(()),
                Err(_) => tokio::time::sleep(Duration::from_millis(500)).await,
            }
        }
        Err(SidecarError::new(
            SidecarErrorKind::HandshakeTimeout,
            "worker did not become ready within timeout",
        ))
    };

    let outcome = timeout(config.ready_timeout, ready_future).await;
    match outcome {
        Ok(Ok(())) => Ok((child, rpc)),
        Ok(Err(e)) => {
            // Clean up the child process so it does not leak as a zombie.
            let _ = child.kill().await;
            let stderr_text = stderr_buf.lock().await.clone();
            let snippet = stderr_text.chars().take(200).collect::<String>();
            Err(e.with_details(json!({ "stderr": snippet })))
        }
        Err(_) => {
            let _ = child.kill().await;
            let stderr_text = stderr_buf.lock().await.clone();
            let snippet = stderr_text.chars().take(200).collect::<String>();
            Err(SidecarError::new(
                SidecarErrorKind::HandshakeTimeout,
                format!("ready handshake timed out after {:?}", config.ready_timeout),
            )
            .with_details(json!({ "stderr": snippet })))
        }
    }
}
