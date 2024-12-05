// File: src/user.rs
// ❌ Bad example
struct user {  // Wrong: Type names should be PascalCase
    UserName: String,  // Wrong: Field names should be snake_case
    Age: i32,
    email: String,  // Inconsistent naming
}

impl user {
    // Wrong: Missing documentation
    fn new(n: String, a: i32, e: String) -> user {  // Poor parameter names
        user {
            UserName: n,
            Age: a,
            email: e,
        }
    }
}

// ✅ Good example
/// Represents a user in the system
#[derive(Debug, Clone, PartialEq)]
pub struct User {
    /// The user's display name
    username: String,
    /// User's age in years
    age: i32,
    /// Email address for notifications
    email: String,
}

impl User {
    /// Creates a new user with the given details
    ///
    /// # Examples
    /// ```
    /// let user = User::new("alice", 30, "alice@example.com");
    /// ```
    pub fn new(username: String, age: i32, email: String) -> Self {
        Self {
            username,
            age,
            email,
        }
    }
}