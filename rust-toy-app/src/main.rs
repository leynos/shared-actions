use rust_toy_app::cli::Cli;
use clap::Parser;

fn main() {
    let cli = Cli::parse();
    println!("{}", cli.run());
}
