//! Heartbeat watchdog for detecting unresponsive workers.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;
use tokio::task::JoinHandle;

/// Default heartbeat timeout (3x the 5s heartbeat interval).
pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(15);

/// Shared state used to record heartbeats from outside the watchdog task.
///
/// Exposed as a small struct rather than a raw `Arc<Mutex<Option<Instant>>>`
/// so we can atomically reset the edge-trigger flag on the same write, and
/// so callers cannot accidentally record a heartbeat without disarming a
/// pending timeout.
#[derive(Clone)]
pub struct HeartbeatHandle {
    last: Arc<Mutex<Option<Instant>>>,
    /// Set to `true` by the watchdog when it fires, cleared by every
    /// [`HeartbeatHandle::record`] call. Prevents "restart storm": the
    /// monitor loop takes several seconds to actually respawn a worker,
    /// during which the 1s tick would otherwise keep notifying.
    fired: Arc<AtomicBool>,
}

impl HeartbeatHandle {
    /// Record a heartbeat: update timestamp + re-arm the watchdog.
    pub async fn record(&self) {
        *self.last.lock().await = Some(Instant::now());
        self.fired.store(false, Ordering::SeqCst);
    }
}

/// Watchdog that triggers a callback **once per timeout episode** when
/// heartbeats stop.
pub struct HeartbeatWatchdog {
    handle: HeartbeatHandle,
    timeout: Duration,
    on_timeout: Box<dyn Fn() + Send + 'static>,
}

impl HeartbeatWatchdog {
    /// Create a new watchdog with the given timeout and callback.
    ///
    /// Returns the watchdog plus a [`HeartbeatHandle`] the caller can use to
    /// record heartbeats from outside the watchdog task (e.g. from the
    /// RPC notification handler).
    pub fn new<F>(timeout: Duration, on_timeout: F) -> (Self, HeartbeatHandle)
    where
        F: Fn() + Send + 'static,
    {
        let handle = HeartbeatHandle {
            last: Arc::new(Mutex::new(None)),
            fired: Arc::new(AtomicBool::new(false)),
        };
        (
            Self {
                handle: handle.clone(),
                timeout,
                on_timeout: Box::new(on_timeout),
            },
            handle,
        )
    }

    /// Create a watchdog with the default 15s timeout.
    pub fn with_default_timeout<F>(on_timeout: F) -> (Self, HeartbeatHandle)
    where
        F: Fn() + Send + 'static,
    {
        Self::new(DEFAULT_TIMEOUT, on_timeout)
    }

    /// Start the watchdog task. Returns a JoinHandle.
    ///
    /// Checks every 1 second. Fires **once** when the last heartbeat is
    /// older than `timeout`; will not fire again until a heartbeat is
    /// recorded (which re-arms the [`fired`](HeartbeatHandle::fired) flag).
    /// This prevents the monitor loop from being woken up N times for one
    /// N-second silence (restart storm during the kill → spawn → ready
    /// sequence, which can take up to `ready_timeout`).
    pub fn start(self) -> JoinHandle<()> {
        let handle = self.handle.clone();
        let timeout = self.timeout;
        let on_timeout = self.on_timeout;
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(Duration::from_secs(1));
            loop {
                interval.tick().await;
                let timed_out = {
                    let guard = handle.last.lock().await;
                    match *guard {
                        Some(instant) => instant.elapsed() > timeout,
                        None => false, // No heartbeat yet; not a timeout.
                    }
                };
                if !timed_out {
                    continue;
                }
                // Compare-and-swap: only the first tick that observes a
                // fresh timeout fires; subsequent ticks are no-ops until
                // `record` resets the flag.
                if handle
                    .fired
                    .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                    .is_ok()
                {
                    on_timeout();
                }
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;

    /// P0-R2 回归：一次持续 silence 只应触发一次 restart，而不是每秒一次。
    /// 修前 monitor 处理一条 restart（kill→spawn→ready 可达 10s）期间，
    /// watchdog 每秒 send，unbounded channel 堆积 → restart storm。
    #[tokio::test]
    async fn fires_once_per_timeout_episode_not_every_tick() {
        let fire_count = Arc::new(AtomicUsize::new(0));
        let count_clone = Arc::clone(&fire_count);

        let (watchdog, handle) =
            HeartbeatWatchdog::new(Duration::from_millis(50), move || {
                count_clone.fetch_add(1, Ordering::SeqCst);
            });
        let task = watchdog.start();

        // 喂一份心跳，然后停止 → 超时开始
        handle.record().await;

        // watchdog 每 1s tick 一次；sleep 跨过至少两个 tick 确认"持续
        // silence 只 fire 一次"（timeout=50ms 时，首个 tick 起即超时）
        tokio::time::sleep(Duration::from_millis(2300)).await;

        let fires = fire_count.load(Ordering::SeqCst);
        task.abort();
        assert_eq!(
            fires, 1,
            "持续 silence 期内应只 fire 一次（边沿触发），实际 {fires} 次 → restart storm 回归"
        );
    }

    /// 心跳恢复后 watchdog 必须重新武装：再次 silence 应再 fire 一次。
    #[tokio::test]
    async fn re_arms_after_a_fresh_heartbeat() {
        let fire_count = Arc::new(AtomicUsize::new(0));
        let count_clone = Arc::clone(&fire_count);
        let (watchdog, handle) =
            HeartbeatWatchdog::new(Duration::from_millis(50), move || {
                count_clone.fetch_add(1, Ordering::SeqCst);
            });
        let task = watchdog.start();

        // 第一轮 silence → fire 1 次（跨过 1s tick 边界）
        handle.record().await;
        tokio::time::sleep(Duration::from_millis(1300)).await;
        assert_eq!(fire_count.load(Ordering::SeqCst), 1);

        // 恢复心跳（重新武装）→ 再 silence → 应再 fire
        handle.record().await;
        tokio::time::sleep(Duration::from_millis(1300)).await;
        let total = fire_count.load(Ordering::SeqCst);
        task.abort();
        assert_eq!(total, 2, "心跳恢复后未重新武装，第二轮 silence 没触发 restart");
    }

    #[tokio::test]
    async fn no_heartbeat_means_no_timeout() {
        // last 仍是 None 时不应 fire（初始连接由 spawn 的 ready_timeout 兜底）
        let fired = Arc::new(AtomicUsize::new(0));
        let c = Arc::clone(&fired);
        let (watchdog, _handle) =
            HeartbeatWatchdog::new(Duration::from_millis(50), move || {
                c.fetch_add(1, Ordering::SeqCst);
            });
        let task = watchdog.start();
        tokio::time::sleep(Duration::from_millis(1300)).await;
        task.abort();
        assert_eq!(fired.load(Ordering::SeqCst), 0);
    }
}
