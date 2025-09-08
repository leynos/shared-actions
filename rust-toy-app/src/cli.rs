//! Command-line interface for `rust-toy-app`. Provides argument parsing and behaviour.
use clap::{Parser, CommandFactory};

#[derive(Parser, Debug)]
#[command(name = "rust-toy-app", about = "A toy app for E2E tests.", version, author)]
pub struct Cli {
    /// Name to greet
    #[arg(short, long, value_name = "NAME")]
    pub name: Option<String>,
}

impl Cli {
    /// Produce the greeting for the provided name, defaulting to "world".
    #[must_use]
    pub fn run(&self) -> String {
        let name = self.name.as_deref().unwrap_or("world");
        format!("Hello, {name}!")
    }
}

/// Return the `clap::Command` for this CLI (used by `build.rs` and tests).
pub fn command() -> clap::Command {
    Cli::command()
}
