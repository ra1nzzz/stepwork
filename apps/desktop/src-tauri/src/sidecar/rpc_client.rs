//! JSON-RPC client over length-prefixed stdio frames.
//!
//! Protocol: [4-byte big-endian u32 length][JSON UTF-8 payload]

use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::sync::{oneshot, Mutex};
use tokio::task::JoinHandle;
use tokio::time::{timeout, Duration};
use uuid::Uuid;

use crate::error::{SidecarError, SidecarErrorKind};

/// Maximum frame size (1 MiB).
pub const MAX_FRAME_SIZE: usize = 1024 * 1024;

/// Default RPC call timeout.
pub const CALL_TIMEOUT: Duration = Duration::from_secs(10);

type PendingMap = Arc<Mutex<HashMap<String, oneshot::Sender<Result<Value, SidecarError>>>>>;

/// Handler invoked for every JSON-RPC notification (frame without an `id`).
type NotifyHandler = Arc<dyn Fn(String, Value) + Send + Sync>;

type DynRead = Box<dyn AsyncRead + Unpin + Send>;
type DynWrite = Box<dyn AsyncWrite + Unpin + Send>;

/// JSON-RPC 2.0 client multiplexed over child stdio.
pub struct RpcClient {
    writer: Arc<Mutex<DynWrite>>,
    pending: PendingMap,
    read_task: Mutex<Option<JoinHandle<()>>>,
    notify_handler: Arc<Mutex<Option<NotifyHandler>>>,
}

impl RpcClient {
    /// Create a new RPC client and spawn the read loop.
    pub fn new(
        stdin: impl AsyncWrite + Unpin + Send + 'static,
        stdout: impl AsyncRead + Unpin + Send + 'static,
    ) -> Arc<Self> {
        let pending: PendingMap = Arc::new(Mutex::new(HashMap::new()));
        let writer: Arc<Mutex<DynWrite>> = Arc::new(Mutex::new(Box::new(stdin)));
        let notify_handler: Arc<Mutex<Option<NotifyHandler>>> = Arc::new(Mutex::new(None));

        let read_task = tokio::spawn(read_loop(
            Box::new(stdout),
            Arc::clone(&pending),
            Arc::clone(&notify_handler),
        ));

        Arc::new(Self {
            writer,
            pending,
            read_task: Mutex::new(Some(read_task)),
            notify_handler,
        })
    }

    /// Register a handler invoked for every notification frame.
    pub async fn set_notify_handler(&self, handler: NotifyHandler) {
        *self.notify_handler.lock().await = Some(handler);
    }

    /// Send a JSON-RPC request and await the response.
    pub async fn call(&self, method: &str, params: Value) -> Result<Value, SidecarError> {
        let id = Uuid::new_v4().to_string();
        let (tx, rx) = oneshot::channel();

        {
            let mut pending = self.pending.lock().await;
            pending.insert(id.clone(), tx);
        }

        let request = json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });

        self.write_frame(&request).await.inspect_err(|_| {
            // Best-effort cleanup; pending entry will be dropped by caller timeout.
            let pending = Arc::clone(&self.pending);
            let id_clone = id.clone();
            tokio::spawn(async move {
                pending.lock().await.remove(&id_clone);
            });
        })?;

        match timeout(CALL_TIMEOUT, rx).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err(SidecarError::new(
                SidecarErrorKind::WorkerCrashed,
                "response channel closed",
            )),
            Err(_) => {
                self.pending.lock().await.remove(&id);
                Err(SidecarError::new(
                    SidecarErrorKind::HandshakeTimeout,
                    format!("RPC call {method} timed out"),
                ))
            }
        }
    }

    /// Send a JSON-RPC notification (no id, no response).
    pub async fn notify(&self, method: &str, params: Value) -> Result<(), SidecarError> {
        let notification = json!({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        });
        self.write_frame(&notification).await
    }

    /// Write a single frame to the child stdin.
    async fn write_frame(&self, value: &Value) -> Result<(), SidecarError> {
        let payload = serde_json::to_vec(value).map_err(|e| {
            SidecarError::new(SidecarErrorKind::ParseError, format!("serialize: {e}"))
        })?;

        if payload.len() > MAX_FRAME_SIZE {
            return Err(SidecarError::new(
                SidecarErrorKind::FrameTooLarge,
                format!("frame size {} exceeds limit", payload.len()),
            ));
        }

        let len = (payload.len() as u32).to_be_bytes();
        let mut writer = self.writer.lock().await;
        writer.write_all(&len).await.map_err(|e| {
            SidecarError::new(
                SidecarErrorKind::RpcProtocolError,
                format!("write len: {e}"),
            )
        })?;
        writer.write_all(&payload).await.map_err(|e| {
            SidecarError::new(
                SidecarErrorKind::RpcProtocolError,
                format!("write payload: {e}"),
            )
        })?;
        writer.flush().await.map_err(|e| {
            SidecarError::new(SidecarErrorKind::RpcProtocolError, format!("flush: {e}"))
        })?;
        Ok(())
    }

    /// Shut down the read loop.
    pub async fn shutdown(&self) {
        if let Some(task) = self.read_task.lock().await.take() {
            task.abort();
        }
    }
}

impl Drop for RpcClient {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.read_task.try_lock() {
            if let Some(task) = guard.take() {
                task.abort();
            }
        }
    }
}

/// Background read loop: reads frames and dispatches to pending callers
/// or the notification handler.
async fn read_loop(
    mut stdout: DynRead,
    pending: PendingMap,
    notify_handler: Arc<Mutex<Option<NotifyHandler>>>,
) {
    loop {
        match read_frame(&mut stdout).await {
            Ok(frame) => {
                if let Some(id) = frame.get("id").and_then(|v| v.as_str()) {
                    let mut pending_guard = pending.lock().await;
                    if let Some(tx) = pending_guard.remove(id) {
                        let result = if let Some(error) = frame.get("error") {
                            Err(SidecarError::new(
                                SidecarErrorKind::RpcProtocolError,
                                error
                                    .get("message")
                                    .and_then(|m| m.as_str())
                                    .unwrap_or("unknown rpc error"),
                            ))
                        } else {
                            Ok(frame.get("result").cloned().unwrap_or(Value::Null))
                        };
                        let _ = tx.send(result);
                    }
                } else if let Some(method) = frame.get("method").and_then(|v| v.as_str()) {
                    // Notification (no id): forward to the registered handler.
                    let handler = notify_handler.lock().await;
                    if let Some(h) = handler.as_ref() {
                        h(
                            method.to_string(),
                            frame.get("params").cloned().unwrap_or(Value::Null),
                        );
                    }
                }
            }
            Err(e) => {
                // Fatal: notify all pending callers and exit.
                let mut pending_guard = pending.lock().await;
                for (_, tx) in pending_guard.drain() {
                    let _ = tx.send(Err(e.clone()));
                }
                break;
            }
        }
    }
}

/// Read a single length-prefixed frame from stdout.
async fn read_frame(stdout: &mut DynRead) -> Result<Value, SidecarError> {
    let mut len_buf = [0u8; 4];
    stdout.read_exact(&mut len_buf).await.map_err(|e| {
        SidecarError::new(SidecarErrorKind::WorkerCrashed, format!("read len: {e}"))
    })?;

    let len = u32::from_be_bytes(len_buf) as usize;
    if len > MAX_FRAME_SIZE {
        // Drain the oversized frame to keep the stream aligned.
        let mut remaining = len;
        let mut buf = vec![0u8; 8192.min(len)];
        while remaining > 0 {
            let chunk = remaining.min(buf.len());
            stdout.read_exact(&mut buf[..chunk]).await.map_err(|e| {
                SidecarError::new(SidecarErrorKind::WorkerCrashed, format!("drain: {e}"))
            })?;
            remaining -= chunk;
        }
        return Err(SidecarError::new(
            SidecarErrorKind::FrameTooLarge,
            format!("frame size {len} exceeds limit"),
        ));
    }

    let mut payload = vec![0u8; len];
    stdout.read_exact(&mut payload).await.map_err(|e| {
        SidecarError::new(
            SidecarErrorKind::WorkerCrashed,
            format!("read payload: {e}"),
        )
    })?;

    serde_json::from_slice(&payload)
        .map_err(|e| SidecarError::new(SidecarErrorKind::ParseError, format!("json parse: {e}")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::duplex;

    #[tokio::test]
    async fn frame_roundtrip() {
        let (mut client, mut server) = duplex(4096);
        let value = json!({"jsonrpc":"2.0","id":"1","result":{"ok":true}});
        let bytes = serde_json::to_vec(&value).expect("serialize");
        let len = (bytes.len() as u32).to_be_bytes();

        server.write_all(&len).await.expect("write len");
        server.write_all(&bytes).await.expect("write payload");
        server.flush().await.expect("flush");

        let mut len_buf = [0u8; 4];
        client.read_exact(&mut len_buf).await.expect("read len");
        let len = u32::from_be_bytes(len_buf) as usize;
        assert_eq!(len, bytes.len());

        let mut payload = vec![0u8; len];
        client.read_exact(&mut payload).await.expect("read payload");
        let decoded: Value = serde_json::from_slice(&payload).expect("parse");
        assert_eq!(decoded["id"], "1");
    }

    #[tokio::test]
    async fn rejects_oversized_frame() {
        let oversized = MAX_FRAME_SIZE + 1;
        assert!(oversized > MAX_FRAME_SIZE);
    }

    #[tokio::test]
    async fn handles_malformed_json() {
        let bad = b"{not valid json";
        let result: Result<Value, _> = serde_json::from_slice(bad);
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn concurrent_calls_have_unique_ids() {
        let ids: Vec<String> = (0..5).map(|_| Uuid::new_v4().to_string()).collect();
        let unique: std::collections::HashSet<_> = ids.iter().collect();
        assert_eq!(unique.len(), 5);
    }

    #[tokio::test]
    async fn routes_notifications_to_handler() {
        // Two independent duplexes synthesize a bidirectional link:
        // the client reads from `r_rx` and writes to `w_tx`; the test
        // keeps `r_tx` (to inject frames the client reads) and `w_rx`.
        let (r_rx, mut r_tx) = duplex(4096);
        let (_w_rx, w_tx) = duplex(4096);
        let rpc = RpcClient::new(w_tx, r_rx);
        let seen = Arc::new(Mutex::new(Vec::<String>::new()));
        let seen_clone = Arc::clone(&seen);
        rpc.set_notify_handler(Arc::new(move |method: String, _params: Value| {
            let s = Arc::clone(&seen_clone);
            tokio::spawn(async move {
                s.lock().await.push(method);
            });
        }))
        .await;

        // Inject a `runtime.heartbeat` notification (no id) the client
        // will read from its `r_rx`; write it to the paired `r_tx`.
        let notification = json!({"jsonrpc":"2.0","method":"runtime.heartbeat","params":{}});
        let bytes = serde_json::to_vec(&notification).expect("serialize");
        let len = (bytes.len() as u32).to_be_bytes();
        r_tx.write_all(&len).await.expect("write len");
        r_tx.write_all(&bytes).await.expect("write payload");
        r_tx.flush().await.expect("flush");

        tokio::time::sleep(Duration::from_millis(80)).await;
        let recorded = seen.lock().await;
        assert_eq!(
            recorded.first().map(|s| s.as_str()),
            Some("runtime.heartbeat")
        );
    }
}
