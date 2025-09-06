pub mod cli;

#[cfg(test)]
mod tests {
    use super::cli::Cli;

    #[test]
    fn run_returns_greeting() {
        let cli = Cli { name: Some("Alice".into()) };
        assert_eq!(cli.run(), "Hello, Alice!");
    }
}
