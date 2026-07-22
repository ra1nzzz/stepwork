//! Tauri Commands 集合（W1 仅转发健康检查，W2 接入 Command Bus，
//! W3-W4 增加 dispatch_command 作为前端与 worker 的唯一桥接点）。

pub mod health;
pub mod dispatch;
