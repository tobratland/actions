# Rust Development Manual

## Code Style & Organization

### File Structure
- Source files in `src/`
- Tests in `tests/`
- Benchmarks in `benches/`
- Examples in `examples/`

### Naming Conventions
- Types/Traits: `PascalCase` (e.g., `HashMap`, `Iterator`)
- Variables/Functions: `snake_case` (e.g., `user_input`, `calculate_total`)
- Constants: `SCREAMING_SNAKE_CASE` (e.g., `MAX_BUFFER_SIZE`)
- Modules: `snake_case` (e.g., `error_handling`)

### Documentation
- Every public item must have documentation comments
- Use `///` for doc comments, `//` for implementation notes
- Include examples in doc comments when appropriate
- Document panics, errors, and safety assumptions

### Error Handling
- Prefer `Result` over `panic!`
- Custom errors should implement `std::error::Error`
- Use `anyhow` for application code, `thiserror` for libraries
- Include context with `.context()` or `.with_context()`

### Testing
- Unit tests in the same file as the code
- Integration tests in `tests/`
- Property-based testing with `proptest` for complex logic
- Benchmark critical paths

### Dependencies
- Review dependencies' security and maintenance status
- Minimize dependency count
- Pin versions in `Cargo.toml`
- Regular dependency updates via `cargo update`

### Performance
- Use release builds for benchmarking
- Profile before optimizing
- Consider using `parking_lot` instead of std mutexes
- Avoid allocations in hot paths

### Safety
- Minimize usage of `unsafe`
- Document all unsafe blocks
- Prefer safe abstractions
- Use `#[deny(unsafe_code)]` when possible

### Tooling
- Use `clippy` with recommended lints
- Enable pedantic warnings:
```toml
[lints.rust]
warnings = "deny"
unsafe_code = "forbid"

[lints.clippy]
pedantic = "warn"
nursery = "warn"
```

### Code Organization
- One type per file unless tightly coupled
- Maximum file length: 500 lines
- Maximum function length: 50 lines
- Maximum line length: 100 characters

## Review Process
1. Run `cargo clippy`
2. Run `cargo fmt`
3. Run tests: `cargo test`
4. Update documentation if needed
5. Peer review required