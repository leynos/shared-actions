//! Shared helpers, fixtures, and utilities for rust-toy-app integration tests.

use glob::glob;
use std::path::Path;

// `allow` rather than `expect`: this module is compiled into several test
// crates, and the helper is dead only in those that skip the default-target
// path.
#[allow(dead_code)]
pub fn assert_manpage_exists() {
    let target = std::env::var("CARGO_TARGET_DIR").unwrap_or_else(|_| "target".into());
    assert_manpage_exists_in(Path::new(&target));
}

pub fn assert_manpage_exists_in(root: impl AsRef<Path>) {
    let root = root.as_ref();
    let patterns = [
        // Stable location written by build.rs since PR #260.
        root.join("generated-man/*/debug/rust-toy-app.1"),
        root.join("generated-man/*/release/rust-toy-app.1"),
        // Legacy OUT_DIR location, retained for older build artefacts.
        root.join("debug/build/rust-toy-app-*/out/rust-toy-app.1"),
        root.join("release/build/rust-toy-app-*/out/rust-toy-app.1"),
    ];
    let display_patterns: Vec<String> =
        patterns.iter().map(|p| p.display().to_string()).collect();

    let found = patterns.iter().any(|pattern| {
        glob(pattern.to_str().expect("pattern should be valid UTF-8"))
            .expect("valid glob")
            .any(|entry| entry.map(|p| p.exists()).unwrap_or(false))
    });
    assert!(found, "man page not found; searched: {:?}", display_patterns);
}
