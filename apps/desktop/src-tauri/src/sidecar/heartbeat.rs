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
    pub fn new<F>(timeout: Duration, on_timeout: F) -> Self
    where
        F: Fn() + Send + 'static,
    {
        Self {
            last_heartbeat: Arc::new(Mutex::new(None)),
            timeout,
            on_timeout: Box::new(on_timeout),
        }
    }

    /// Create a watchdog with the default 15s timeout.
    pub fn with_default_timeout<F>(on_timeout: F) -> Self
    where
        F: Fn() + Send + 'static,
    {
        Self::new(DEFAULT_TIMEOUT, on_timeout)
    }

    /// Record a heartbeat received from the worker.
    pub async fn record_heartbeat(&self) {
        let mut guard = self.last_heartbeat.lock().await;
        *guard = Some(Instant::now());
    }

    /// Start the watchdog task. Returns a JoinHandle.
    ///
    /// Checks every 1 second; if `last_heartbeat` is older than `timeout`,
    /// invokes `on_timeout` and continues monitoring.
    pub fn start(self) -> JoinHandle<()> {
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(Duration::from_secs(1));
            loop {
                interval.tick().await;
                let timed_out = {
                    let guard = self.last_heartbeat.lock().await;
                    match *guard {
                        Some(instant) => instant.elapsed() > self.timeout,
                        None => false, // No heartbeat yet; not a timeout.
                    }
                };
                if timed_out {
                    (self.on_timeout)();
                }
            }
        })
    }
}
