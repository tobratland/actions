// File: src/config.rs
// ❌ Bad example
const DEFAULT_TIMEOUT: i32 = 30;  // Wrong: No documentation, no type suffix
const maxConnections: i32 = 100;  // Wrong: Wrong naming convention
static mut GLOBAL_CONFIG: Option<Config> = None;  // Wrong: Unsafe global state

// ✅ Good example
/// Default timeout in seconds for API requests
pub const DEFAULT_TIMEOUT_SECS: i32 = 30;

/// Maximum number of simultaneous connections
pub const MAX_CONNECTIONS: i32 = 100;

/// Configuration for the application
#[derive(Debug, Clone)]
pub struct Config {
    /// Timeout for API requests in seconds
    timeout: i32,
    /// Maximum number of connections
    max_connections: i32,
}

impl Config {
    /// Creates a new configuration with default values
    pub fn new() -> Self {
        Self {
            timeout: DEFAULT_TIMEOUT_SECS,
            max_connections: MAX_CONNECTIONS,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_config_defaults() {
        let config = Config::new();
        assert_eq!(config.timeout, DEFAULT_TIMEOUT_SECS);
        assert_eq!(config.max_connections, MAX_CONNECTIONS);
    }
}