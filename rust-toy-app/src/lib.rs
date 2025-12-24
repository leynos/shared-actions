//! Library crate for `rust-toy-app`. Exposes the CLI for reuse in tests and build scripts.
//!
//! # Test Fixture Notice
//!
//! This crate serves as a **test fixture** for validating the [`generate-coverage`]
//! GitHub Action. The test suite demonstrates various testing patterns (unit tests
//! with rstest fixtures, cucumber-rs BDD, rstest-style BDD) rather than serving as
//! a functional product.
//!
//! The simple greeting functionality exists solely to provide meaningful code paths
//! for coverage collection during CI validation.
//!
//! [`generate-coverage`]: https://github.com/anthropics/shared-actions/tree/main/.github/actions/generate-coverage

pub mod cli;

#[cfg(test)]
mod tests {
    use super::cli::Cli;
    use rstest::{fixture, rstest};

    // =========================================================================
    // Fixtures
    // =========================================================================

    /// Fixture providing a CLI instance configured with a custom name.
    #[fixture]
    fn cli_with_name() -> Cli {
        Cli {
            name: Some("TestUser".into()),
        }
    }

    /// Fixture providing a CLI instance with no name (default greeting).
    #[fixture]
    fn cli_default() -> Cli {
        Cli { name: None }
    }

    /// Fixture providing a CLI with a configurable name.
    /// Defaults to "Alice" if no name is specified.
    #[fixture]
    fn cli_named(#[default(Some("Alice".into()))] name: Option<String>) -> Cli {
        Cli { name }
    }

    // =========================================================================
    // Parametrized Tests
    // =========================================================================

    /// Core greeting functionality test with parametrized cases.
    #[rstest]
    #[case::named(Some("Alice".into()), "Hello, Alice!")]
    #[case::default(None, "Hello, world!")]
    fn run_returns_greeting(#[case] name: Option<String>, #[case] expected: &str) {
        let cli = Cli { name };
        assert_eq!(cli.run(), expected);
    }

    /// Verify greeting format consistency across various names.
    #[rstest]
    #[case::simple("Bob")]
    #[case::with_spaces("Charlie Brown")]
    #[case::unicode("Müller")]
    #[case::numbers("Agent007")]
    fn greeting_format_is_consistent(#[case] name: &str) {
        let cli = Cli {
            name: Some(name.into()),
        };
        let result = cli.run();
        assert!(result.starts_with("Hello, "), "Should start with 'Hello, '");
        assert!(result.ends_with('!'), "Should end with '!'");
        assert!(result.contains(name), "Should contain the name");
    }

    // =========================================================================
    // Fixture-based Tests
    // =========================================================================

    /// Test using the cli_with_name fixture.
    #[rstest]
    fn fixture_with_name_greets_test_user(cli_with_name: Cli) {
        assert_eq!(cli_with_name.run(), "Hello, TestUser!");
    }

    /// Test using the cli_default fixture.
    #[rstest]
    fn fixture_default_greets_world(cli_default: Cli) {
        assert_eq!(cli_default.run(), "Hello, world!");
    }

    /// Test using the configurable cli_named fixture with default.
    #[rstest]
    fn fixture_named_uses_default(cli_named: Cli) {
        assert_eq!(cli_named.run(), "Hello, Alice!");
    }

    /// Test using the configurable cli_named fixture with custom value.
    #[rstest]
    fn fixture_named_with_custom(#[with(Some("Custom".into()))] cli_named: Cli) {
        assert_eq!(cli_named.run(), "Hello, Custom!");
    }

    // =========================================================================
    // Edge Case Tests
    // =========================================================================

    /// Empty string name produces greeting with empty name.
    #[rstest]
    fn empty_name_produces_minimal_greeting() {
        let cli = Cli {
            name: Some(String::new()),
        };
        assert_eq!(cli.run(), "Hello, !");
    }

    /// Whitespace-only name is preserved in the greeting.
    #[rstest]
    fn whitespace_name_is_preserved() {
        let cli = Cli {
            name: Some("  ".into()),
        };
        assert_eq!(cli.run(), "Hello,   !");
    }

    /// Very long name is handled correctly.
    #[rstest]
    fn long_name_is_handled() {
        let long_name = "A".repeat(1000);
        let cli = Cli {
            name: Some(long_name.clone()),
        };
        let result = cli.run();
        assert_eq!(result, format!("Hello, {}!", long_name));
    }
}
