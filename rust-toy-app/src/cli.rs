//! Command-line interface for `rust-toy-app`. Provides argument parsing and behaviour.
use clap::{Parser, CommandFactory};

#[derive(Parser, Debug)]
#[command(name = "rust-toy-app", about = "A toy app for E2E tests.")]
pub struct Cli {
    /// Name to greet
    #[arg(short, long)]
    pub name: Option<String>,
}

impl Cli {
    /// Produce the greeting for the provided name, defaulting to "world".
    pub fn run(&self) -> String {
        let name = self.name.as_deref().unwrap_or("world");
        format!("Hello, {name}!")
    }
}

pub fn command() -> clap::Command {
    Cli::command()
}
