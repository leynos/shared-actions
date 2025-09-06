use rust_toy_app::Cli;
use clap::Parser;

fn main() {
    let cli = Cli::parse();
    println!("{}", cli.run());
}
