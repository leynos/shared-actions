use clap::{Parser, CommandFactory};

#[derive(Parser, Debug)]
#[command(name = "rust-toy-app", about = "A toy app for E2E tests.")]
pub struct Cli {
    /// Name to greet
    #[arg(short, long)]
    pub name: Option<String>,
}

impl Cli {
    pub fn run(&self) -> String {
        let name = self.name.clone().unwrap_or_else(|| "world".to_string());
        format!("Hello, {name}!")
    }
}

pub fn command() -> clap::Command {
    Cli::command()
}
