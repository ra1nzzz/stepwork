//! Sidecar subsystem: process lifecycle, RPC client, heartbeat watchdog.

pub mod heartbeat;
pub mod rpc_client;
pub mod spawn;

pub use heartbeat::HeartbeatWatchdog;
pub use rpc_client::RpcClient;
pub use spawn::{spawn_sidecar, SpawnConfig};
