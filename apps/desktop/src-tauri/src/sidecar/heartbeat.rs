//! Heartbeat watchdog for detecting unresponsive workers.

use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;
use tokio::task::JoinHandle;

/// Default heartbeat timeout (3x the 5s heartbeat interval).
pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(15);

/// Watchdog that triggers a callback when heartbeats stop.
pub struct HeartbeatWatchdog {
    last_heartbeat: Arc<Mutex<Option<Instant>>>,
    timeout: Duration,
    on_timeout: Box<dyn Fn() + Send + 'static>,
}

impl HeartbeatWatchdog {
    /// Create a new watchdog with the given timeout and callback.
    ///
    /// Returns the watchdog plus a shared `Arc` the caller can use to
    /// record heartbeats from outside the watchdog task (e.g. from the
    /// RPC notification handler).
    pub fn new<F>(timeout: Duration, on_timeout: F) -> (Self, Arc<Mutex<Option<Instant>>>)
    where
        F: Fn() + Send + 'static,
    {
        let arc = Arc::new(Mutex::new(None));
        (
            Self {
                last_heartbeat: Arc::clone(&arc),
                timeout,
                on_timeout: Box::new(on_timeout),
            },
            arc,
        )
    }

    /// Create a watchdog with the default 15s timeout.
    pub fn with_default_timeout<F>(on_timeout: F) -> (Self, Arc<Mutex<Option<Instant>>>)
    where
        F: Fn() + Send + 'static,
    {
        Self::new(DEFAULT_TIMEOUT, on_timeout)
    }

    /// Record a heartbeat received from the worker.
    pub async fn record_heartbeat(&self) {
        *self.last_heartbeat.lock().await = Some(Instant::now());
    }

    /// Start the watchdog task. Returns a JoinHandle.
    ///
    /// Checks every 1 second; if `last_heartbeat` is older than `timeout`,
    /// invokes `on_timeout` and continues monitoring.
    pub fn start(self) -> JoinHandle<()> {
        let inner = Arc::clone(&self.last_heartbeat);
        let timeout = self.timeout;
        let on_timeout = self.on_timeout;
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(Duration::from_secs(1));
            loop {
                interval.tick().await;
                let timed_out = {
                    let guard = inner.lock().await;
                    match *guard {
                        Some(instant) => instant.elapsed() > timeout,
                        None => false, // No heartbeat yet; not a timeout.
                    }
                };
                if timed_out {
                    on_timeout();
                }
            }
        })
    }
}
