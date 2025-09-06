//! Library crate for `rust-toy-app`. Exposes the CLI for reuse in tests and build scripts.
pub mod cli;

#[cfg(test)]
mod tests {
    use super::cli::Cli;
    use rstest::rstest;

    #[rstest]
    #[case(Some("Alice".into()), "Hello, Alice!")]
    #[case(None, "Hello, world!")]
    fn run_returns_greeting(#[case] name: Option<String>, #[case] expected: &str) {
        let cli = Cli { name };
        assert_eq!(cli.run(), expected);
    }
}
