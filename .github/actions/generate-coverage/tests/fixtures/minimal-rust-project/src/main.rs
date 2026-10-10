//! Minimal binary so the coverage fixture has something to instrument.

/// Return the greeting the fixture prints.
///
/// Kept as its own function so the test below, rather than `main`, is what
/// the coverage run actually measures.
#[must_use]
fn greeting() -> &'static str {
    "hello from the coverage fixture"
}

fn main() {
    println!("{}", greeting());
}

#[cfg(test)]
mod tests {
    use super::greeting;

    #[test]
    fn greeting_is_not_empty() {
        assert!(!greeting().is_empty());
    }
}
