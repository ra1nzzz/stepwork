//! 调试模式配置（W9 增补）
//!
//! 配置文件：`$STEPWORK_HOME/debug.json`
//! - `console`: 是否在前端显示统一控制台（订阅 worker 日志）
//! - `python_window`: 是否显示 Python 子进程控制台窗口（仅 Windows）
//! - `log_level`: worker 日志级别
//!
//! 文件缺失时使用默认值（开发期默认开启控制台、隐藏 Python 窗口）。

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::RwLock;

/// 调试模式配置
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DebugConfig {
    /// 显示前端统一控制台（订阅 worker stderr 日志流）
    #[serde(default = "default_console")]
    pub console: bool,
    /// 显示 Python 子进程控制台窗口（仅 Windows 生效）
    #[serde(default = "default_python_window")]
    pub python_window: bool,
    /// worker 日志级别
    #[serde(default = "default_log_level")]
    pub log_level: String,
}

fn default_console() -> bool {
    true
}
fn default_python_window() -> bool {
    false
}
fn default_log_level() -> String {
    "info".to_string()
}

impl Default for DebugConfig {
    fn default() -> Self {
        Self {
            console: default_console(),
            python_window: default_python_window(),
            log_level: default_log_level(),
        }
    }
}

/// 解析 `$STEPWORK_HOME` 目录
///
/// 缺省值为 `~/STEPWORK`，与 Python worker 保持一致
/// （worker/runtime/bootstrap.py: `os.environ.get("STEPWORK_HOME") or
/// str(Path.home() / "STEPWORK")`）——shell 与 worker 必须解析出同一个
/// 目录，否则 debug.json / stepwork.db / logs 会分家。
/// 空字符串视同未设置（与 Python 的 falsy 语义对齐）。
pub fn resolve_stepwork_home() -> PathBuf {
    if let Ok(home) = std::env::var("STEPWORK_HOME") {
        if !home.is_empty() {
            return PathBuf::from(home);
        }
    }
    // Path.home() 等价：Windows 用 USERPROFILE，Unix 用 HOME
    let user_home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| std::env::temp_dir());
    user_home.join("STEPWORK")
}

/// debug.json 配置文件路径
pub fn debug_config_path() -> PathBuf {
    resolve_stepwork_home().join("debug.json")
}

/// 加载配置；文件缺失或解析失败时返回默认值（不阻塞启动）
pub fn load_config() -> DebugConfig {
    let path = debug_config_path();
    match std::fs::read_to_string(&path) {
        Ok(content) => serde_json::from_str(&content).unwrap_or_default(),
        Err(_) => DebugConfig::default(),
    }
}

/// 保存配置到 `$STEPWORK_HOME/debug.json`
pub fn save_config(config: &DebugConfig) -> std::io::Result<()> {
    let path = debug_config_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let content = serde_json::to_string_pretty(config)?;
    std::fs::write(&path, content)?;
    Ok(())
}

/// 进程内共享的配置句柄（支持运行时热更新）
#[derive(Clone)]
pub struct DebugConfigHandle {
    inner: Arc<RwLock<DebugConfig>>,
}

impl DebugConfigHandle {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(RwLock::new(load_config())),
        }
    }

    pub async fn get(&self) -> DebugConfig {
        self.inner.read().await.clone()
    }

    pub async fn set(&self, config: DebugConfig) -> std::io::Result<()> {
        save_config(&config)?;
        *self.inner.write().await = config;
        Ok(())
    }
}

impl Default for DebugConfigHandle {
    fn default() -> Self {
        Self::new()
    }
}
