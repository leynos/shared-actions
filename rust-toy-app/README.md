# rust-toy-app

> **Test Fixture** - This application serves as a test fixture for validating the
> [`generate-coverage`](../.github/actions/generate-coverage/) GitHub Action.
> It is **not** intended as a functional product.

## Purpose

This simple Command Line Interface (CLI) application demonstrates various Rust testing patterns for
coverage collection validation:

| Test Type | Framework | File(s) | Harness |
|-----------|-----------|---------|---------|
| Unit tests | [rstest](https://crates.io/crates/rstest) | `src/lib.rs` | Standard |
| Integration tests | rstest + assert_cmd | `tests/cli.rs` | Standard |
| Behaviour-Driven Development (BDD) (Gherkin) | [cucumber-rs](https://cucumber-rs.github.io/cucumber/current/) | `tests/cucumber.rs`, `tests/features/*.feature` | Custom (`harness = false`) |
| BDD (rstest-style) | rstest | `tests/bdd.rs` | Standard |

The test suite validates that the `generate-coverage` action correctly:

- Collects coverage from standard `cargo test` runs
- Collects coverage from cucumber-rs tests via `with-cucumber-rs: true`
- Merges coverage reports from multiple test harnesses

## Usage

```bash
# Default greeting
rust-toy-app
# Output: Hello, world!

# Named greeting
rust-toy-app --name Alice
# Output: Hello, Alice!
```

## Running Tests

### All Tests (Standard Harness)

```bash
cargo test
```

This runs unit tests, integration tests, and BDD-style rstest tests.
Note: cucumber-rs tests use a custom harness and run separately.

### Cucumber-rs Tests Only

```bash
cargo test --test cucumber
```

### Specific Test Types

```bash
# Unit tests only
cargo test --lib

# Integration tests (CLI)
cargo test --test cli

# BDD-style rstest tests
cargo test --test bdd
```

## Coverage Collection

This project is designed to work with `cargo llvm-cov` for coverage collection.

### Standard Coverage

```bash
cargo llvm-cov --cobertura --output-path coverage.xml
```

### With Cucumber-rs (as generate-coverage action does it)

```bash
# The action runs this command for cucumber coverage:
cargo llvm-cov -- --test cucumber -- cucumber --features tests/features
```

## generate-coverage Action Integration

The test suite is designed to be invoked via the `generate-coverage` action:

```yaml
- uses: ./.github/actions/generate-coverage
  with:
    output-path: coverage.xml
    format: cobertura
    with-cucumber-rs: true
    cucumber-rs-features: tests/features
```

### Action Inputs Used

| Input | Value | Description |
|-------|-------|-------------|
| `with-cucumber-rs` | `true` | Enables cucumber-rs coverage collection |
| `cucumber-rs-features` | `tests/features` | Path to Gherkin feature files |

## Test Structure

```text
rust-toy-app/
  src/
    lib.rs          # Library with unit tests (rstest fixtures)
    cli.rs          # CLI definition
    main.rs         # Binary entrypoint
  tests/
    cli.rs          # Integration tests (assert_cmd)
    cucumber.rs     # cucumber-rs test runner (harness = false)
    bdd.rs          # BDD-style rstest tests (standard harness)
    features/
      greeting.feature  # Gherkin scenarios for greeting
      cli.feature       # Gherkin scenarios for CLI behaviour
```

## Test Patterns Demonstrated

### rstest Fixtures (`src/lib.rs`)

```rust
#[fixture]
fn cli_with_name() -> Cli {
    Cli { name: Some("TestUser".into()) }
}

#[rstest]
fn fixture_with_name_greets_test_user(cli_with_name: Cli) {
    assert_eq!(cli_with_name.run(), "Hello, TestUser!");
}
```

### rstest Parametrized Tests (`src/lib.rs`)

```rust
#[rstest]
#[case::named(Some("Alice".into()), "Hello, Alice!")]
#[case::default(None, "Hello, world!")]
fn run_returns_greeting(#[case] name: Option<String>, #[case] expected: &str) {
    let cli = Cli { name };
    assert_eq!(cli.run(), expected);
}
```

### Cucumber-rs BDD (`tests/cucumber.rs`)

```rust
#[given(expr = "a name {string} is provided")]
fn name_is_provided(world: &mut GreetingWorld, name: String) {
    world.cli = Some(Cli { name: Some(name) });
}

#[when("the greeting is generated")]
fn greeting_is_generated(world: &mut GreetingWorld) {
    world.greeting = world.cli.as_ref().map(|c| c.run());
}
```

### BDD-style rstest (`tests/bdd.rs`)

```rust
#[rstest]
fn scenario_default_greeting_without_name(mut state: GreetingState) {
    // Given: no name is provided
    state.given_name(None);

    // When: the greeting is generated
    state.when_greeting_generated();

    // Then: the output should be "Hello, world!"
    state.then_greeting_is("Hello, world!");
}
```

## Licence

ISC
