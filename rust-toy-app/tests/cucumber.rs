//! Cucumber-rs test runner for rust-toy-app.
//!
//! # Test Fixture Notice
//!
//! This test file is a **fixture** for validating the [`generate-coverage`] GitHub Action's
//! cucumber-rs integration (`with-cucumber-rs: true`). It is not a functional product test suite.
//!
//! The scenarios in `tests/features/` exercise the greeting functionality to generate
//! meaningful code coverage metrics during CI.
//!
//! ## Running
//!
//! ```bash
//! cargo test --test cucumber
//! ```
//!
//! ## Coverage Collection
//!
//! The `generate-coverage` action invokes this as:
//! ```bash
//! cargo llvm-cov -- --test cucumber -- cucumber --features tests/features
//! ```
//!
//! [`generate-coverage`]: https://github.com/leynos/shared-actions/tree/main/.github/actions/generate-coverage

use std::process::{Command, Output};

use cucumber::{given, then, when, World};
use rust_toy_app::cli::Cli;

/// Path to the Gherkin feature files, relative to the crate root.
///
/// This path must match the `cucumber-rs-features` input when using the
/// `generate-coverage` action with `with-cucumber-rs: true`.
const FEATURES_PATH: &str = "tests/features";

/// World struct maintaining state across cucumber steps.
///
/// Each scenario gets a fresh instance of this struct.
#[derive(Debug, Default, World)]
pub struct GreetingWorld {
    /// The CLI instance being tested (for library-level tests).
    cli: Option<Cli>,
    /// The generated greeting result.
    greeting: Option<String>,
    /// CLI command output for binary tests.
    output: Option<Output>,
}

// =============================================================================
// Greeting feature steps (tests/features/greeting.feature)
// =============================================================================

#[given("no name is provided")]
fn no_name_provided(world: &mut GreetingWorld) {
    world.cli = Some(Cli { name: None });
}

#[given(expr = "a name {string} is provided")]
fn name_is_provided(world: &mut GreetingWorld, name: String) {
    world.cli = Some(Cli { name: Some(name) });
}

#[when("the greeting is generated")]
fn greeting_is_generated(world: &mut GreetingWorld) {
    let cli = world.cli.as_ref().expect("CLI not configured; missing 'Given' step");
    world.greeting = Some(cli.run());
}

#[then(expr = "the output should be {string}")]
fn output_should_be(world: &mut GreetingWorld, expected: String) {
    // Handle both library greeting and CLI stdout output
    if let Some(greeting) = &world.greeting {
        assert_eq!(
            greeting, &expected,
            "Expected greeting '{}' but got '{}'",
            expected, greeting
        );
    } else if let Some(output) = &world.output {
        let stdout = String::from_utf8_lossy(&output.stdout);
        // CLI output includes newline
        let stdout_trimmed = stdout.trim_end();
        assert_eq!(
            stdout_trimmed, expected,
            "Expected stdout '{}' but got '{}'",
            expected, stdout_trimmed
        );
    } else {
        panic!("No greeting or output captured");
    }
}

// =============================================================================
// CLI feature steps (tests/features/cli.feature)
// =============================================================================

#[given("the rust-toy-app binary")]
fn binary_exists(world: &mut GreetingWorld) {
    // Reset state for CLI tests
    world.output = None;
    world.greeting = None;
    world.cli = None;
}

#[when(expr = "I run it with {string}")]
fn run_with_args(world: &mut GreetingWorld, args: String) {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_rust-toy-app"));
    for arg in args.split_whitespace() {
        cmd.arg(arg);
    }
    world.output = Some(cmd.output().expect("failed to execute binary"));
}

#[when("I run it without arguments")]
fn run_without_args(world: &mut GreetingWorld) {
    let output = Command::new(env!("CARGO_BIN_EXE_rust-toy-app"))
        .output()
        .expect("failed to execute binary");
    world.output = Some(output);
}

#[then(expr = "the exit code should be {int}")]
fn exit_code_should_be(world: &mut GreetingWorld, expected: i32) {
    let output = world.output.as_ref().expect("no output captured");
    let actual = output.status.code().expect("no exit code");
    assert_eq!(
        actual, expected,
        "Expected exit code {} but got {}",
        expected, actual
    );
}

/// Enum to select which output stream to check.
enum OutputStream {
    Stdout,
    Stderr,
}

/// Helper function to check if an output stream contains the expected string.
fn check_output_stream_contains(world: &mut GreetingWorld, stream: OutputStream, expected: String) {
    let output = world.output.as_ref().expect("no output captured");
    let (content, stream_name) = match stream {
        OutputStream::Stdout => (String::from_utf8_lossy(&output.stdout), "stdout"),
        OutputStream::Stderr => (String::from_utf8_lossy(&output.stderr), "stderr"),
    };
    assert!(
        content.contains(&expected),
        "Expected {} to contain '{}' but got: {}",
        stream_name,
        expected,
        content
    );
}

#[then(expr = "the output should contain {string}")]
fn output_contains(world: &mut GreetingWorld, expected: String) {
    check_output_stream_contains(world, OutputStream::Stdout, expected);
}

#[then(expr = "the stderr should contain {string}")]
fn stderr_contains(world: &mut GreetingWorld, expected: String) {
    check_output_stream_contains(world, OutputStream::Stderr, expected);
}

fn main() {
    // Run cucumber tests synchronously using futures executor.
    futures::executor::block_on(GreetingWorld::run(FEATURES_PATH));
}
