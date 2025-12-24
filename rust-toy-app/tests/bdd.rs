//! BDD-style tests using rstest for rust-toy-app.
//!
//! # Test Fixture Notice
//!
//! This test file is a **fixture** for validating the [`generate-coverage`] GitHub Action.
//! It demonstrates BDD-style testing using rstest as an alternative to cucumber-rs.
//! It is not a functional product test suite.
//!
//! Unlike cucumber-rs, these tests run with the standard `cargo test` harness and
//! integrate seamlessly with `cargo llvm-cov` without special configuration.
//!
//! ## Running
//!
//! ```bash
//! cargo test --test bdd
//! ```
//!
//! ## Pattern
//!
//! Tests are structured using Given/When/Then comments to maintain BDD semantics
//! while leveraging rstest's powerful fixture and parametrization features.
//!
//! [`generate-coverage`]: https://github.com/anthropics/shared-actions/tree/main/.github/actions/generate-coverage

use rstest::{fixture, rstest};
use rust_toy_app::cli::Cli;

// =============================================================================
// Test State (analogous to cucumber's World)
// =============================================================================

/// State container for BDD scenarios, similar to cucumber's World struct.
#[derive(Debug, Default)]
struct GreetingState {
    cli: Option<Cli>,
    greeting: Option<String>,
}

impl GreetingState {
    /// Given: a CLI with the specified name
    fn given_name(&mut self, name: Option<String>) {
        self.cli = Some(Cli { name });
    }

    /// When: the greeting is generated
    fn when_greeting_generated(&mut self) {
        self.greeting = self.cli.as_ref().map(|c| c.run());
    }

    /// Then: assert the greeting matches expected
    fn then_greeting_is(&self, expected: &str) {
        assert_eq!(
            self.greeting.as_deref(),
            Some(expected),
            "Expected '{}' but got {:?}",
            expected,
            self.greeting
        );
    }
}

// =============================================================================
// Fixtures
// =============================================================================

/// Fixture providing a fresh greeting state for each test.
#[fixture]
fn state() -> GreetingState {
    GreetingState::default()
}

/// Fixture providing a CLI configured with a specific name.
#[fixture]
fn cli_with_name() -> Cli {
    Cli {
        name: Some("FixtureUser".into()),
    }
}

/// Fixture providing a CLI with no name (default greeting).
#[fixture]
fn cli_default() -> Cli {
    Cli { name: None }
}

// =============================================================================
// BDD Scenarios: Greeting Feature
// =============================================================================

/// Scenario: Default greeting without a name
///
/// Mirrors: tests/features/greeting.feature - "Default greeting without a name"
#[rstest]
fn scenario_default_greeting_without_name(mut state: GreetingState) {
    // Given: no name is provided
    state.given_name(None);

    // When: the greeting is generated
    state.when_greeting_generated();

    // Then: the output should be "Hello, world!"
    state.then_greeting_is("Hello, world!");
}

/// Scenario: Personalized greeting with a name
///
/// Mirrors: tests/features/greeting.feature - "Personalized greeting with a name"
#[rstest]
fn scenario_personalized_greeting_with_name(mut state: GreetingState) {
    // Given: a name "Alice" is provided
    state.given_name(Some("Alice".into()));

    // When: the greeting is generated
    state.when_greeting_generated();

    // Then: the output should be "Hello, Alice!"
    state.then_greeting_is("Hello, Alice!");
}

/// Scenario: Greeting with a different name
///
/// Mirrors: tests/features/greeting.feature - "Greeting with a different name"
#[rstest]
fn scenario_greeting_with_different_name(mut state: GreetingState) {
    // Given: a name "Bob" is provided
    state.given_name(Some("Bob".into()));

    // When: the greeting is generated
    state.when_greeting_generated();

    // Then: the output should be "Hello, Bob!"
    state.then_greeting_is("Hello, Bob!");
}

/// Scenario Outline: Various names produce correct greetings
///
/// Mirrors: tests/features/greeting.feature - "Various names produce correct greetings"
#[rstest]
#[case::charlie("Charlie", "Hello, Charlie!")]
#[case::diana("Diana", "Hello, Diana!")]
#[case::eve("Eve", "Hello, Eve!")]
fn scenario_outline_various_names(
    mut state: GreetingState,
    #[case] name: &str,
    #[case] expected: &str,
) {
    // Given: a name "<name>" is provided
    state.given_name(Some(name.into()));

    // When: the greeting is generated
    state.when_greeting_generated();

    // Then: the output should be "Hello, <name>!"
    state.then_greeting_is(expected);
}

// =============================================================================
// Fixture-based Tests
// =============================================================================

/// Test using the cli_with_name fixture directly.
#[rstest]
fn fixture_based_named_greeting(cli_with_name: Cli) {
    // Given: a CLI fixture with "FixtureUser"
    // When: we generate a greeting
    let greeting = cli_with_name.run();

    // Then: the greeting uses the fixture's name
    assert_eq!(greeting, "Hello, FixtureUser!");
}

/// Test using the cli_default fixture directly.
#[rstest]
fn fixture_based_default_greeting(cli_default: Cli) {
    // Given: a CLI fixture with no name
    // When: we generate a greeting
    let greeting = cli_default.run();

    // Then: the greeting is the default
    assert_eq!(greeting, "Hello, world!");
}

// =============================================================================
// Edge Case Scenarios
// =============================================================================

/// Scenario: Empty name produces minimal greeting
#[rstest]
fn scenario_empty_name(mut state: GreetingState) {
    // Given: an empty name is provided
    state.given_name(Some(String::new()));

    // When: the greeting is generated
    state.when_greeting_generated();

    // Then: the output uses the empty name
    state.then_greeting_is("Hello, !");
}

/// Scenario: Whitespace-only name is preserved
#[rstest]
fn scenario_whitespace_name(mut state: GreetingState) {
    // Given: a whitespace-only name is provided
    state.given_name(Some("  ".into()));

    // When: the greeting is generated
    state.when_greeting_generated();

    // Then: the whitespace is preserved in output
    state.then_greeting_is("Hello,   !");
}

/// Scenario: Name with special characters
#[rstest]
#[case::emoji("Hello, World! 🎉")]
#[case::quotes("O'Brien")]
#[case::unicode("Müller")]
#[case::numbers("Agent007")]
fn scenario_special_characters(mut state: GreetingState, #[case] name: &str) {
    // Given: a name with special characters
    state.given_name(Some(name.into()));

    // When: the greeting is generated
    state.when_greeting_generated();

    // Then: the special characters are preserved
    let expected = format!("Hello, {}!", name);
    state.then_greeting_is(&expected);
}
