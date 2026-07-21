//! Sidecar process spawning and handshake.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use serde_json::json;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;
use tokio::time::timeout;

use crate::error::{SidecarError, SidecarErrorKind};
use crate::sidecar::rpc_client::RpcClient;

/// Configuration for spawning a sidecar worker.
pub struct SpawnConfig {
    /// Path to Python interpreter.
    pub python_path: PathBuf,
    /// Python module to run (default "worker.runtime").
    pub worker_module: String,
    /// Session token for this sidecar instance.
    pub session_token: String,
    /// Timeout waiting for `runtime.ready`.
    pub ready_timeout: Duration,
}

impl Default for SpawnConfig {
    fn default() -> Self {
        Self {
            python_path: PathBuf::from("python"),
            worker_module: "worker.runtime".to_string(),
            session_token: uuid::Uuid::new_v4().to_string(),
            ready_timeout: Duration::from_secs(10),
        }
    }
}

/// Spawn a Python worker sidecar and perform the ready handshake.
///
/// Returns the child process and an `Arc<RpcClient>` ready for use.
pub async fn spawn_sidecar(
    config: SpawnConfig,
) -> Result<(Child, Arc<RpcClient>), SidecarError> {
    if config.python_path.is_absolute() && !config.python_path.exists() {
        return Err(SidecarError::new(
            SidecarErrorKind::PythonMissing,
            format!("python not found at {}", config.python_path.display()),
        ));
    }

    let mut child = Command::new(&config.python_path)
        .args(["-m", &config.worker_module])
        .env("STEPWORK_SESSION_TOKEN", &config.session_token)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| {
            SidecarError::new(
                SidecarErrorKind::SpawnFailed,
                format!("failed to spawn python: {e}"),
            )
        })?;

    let stdin = child.stdin.take().ok_or_else(|| {
        SidecarError::new(SidecarErrorKind::SpawnFailed, "stdin unavailable")
    })?;
    let stdout = child.stdout.take().ok_or_else(|| {
        SidecarError::new(SidecarErrorKind::SpawnFailed, "stdout unavailable")
    })?;
    let stderr = child.stderr.take().ok_or_else(|| {
        SidecarError::new(SidecarErrorKind::SpawnFailed, "stderr unavailable")
    })?;

    // Capture stderr into a ring buffer for diagnostics.
    let stderr_buf = Arc::new(Mutex::new(String::new()));
    let stderr_clone = Arc::clone(&stderr_buf);
    tokio::spawn(async move {
        let mut reader = BufReader::new(stderr).lines();
        while let Ok(Some(line)) = reader.next_line().await {
            let mut buf = stderr_clone.lock().await;
            buf.push_str(&line);
            buf.push('\n');
            if buf.len() > 4096 {
                let drain_to = buf.len() - 4096;
                buf.drain(..drain_to);
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
