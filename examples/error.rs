
// File: src/error.rs
// ❌ Bad example
#[derive(Debug)]
struct ApiError {  // Wrong: No documentation
    msg: String,  // Wrong: Not implementing std::error::Error
}

impl ApiError {
    fn new(msg: &str) -> Self {  // Wrong: Not public
        Self {
            msg: msg.to_string(),
        }
    }
}

// ✅ Good example
use thiserror::Error;

/// Errors that can occur during API operations
#[derive(Debug, Error)]
pub enum ApiError {
    /// Failed to authenticate user
    #[error("authentication failed: {0}")]
    AuthError(String),

    /// Required resource not found
    #[error("resource not found: {0}")]
    NotFound(String),

    /// Network or connection error
    #[error("network error: {0}")]
    NetworkError(#[from] std::io::Error),
}
