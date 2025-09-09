
use assert_cmd::prelude::*;
use glob::glob;
use std::process::Command;

#[test]
fn generates_manpage() {
    Command::new("cargo")
        .arg("build")
        .assert()
        .success();

    let target = std::env::var("CARGO_TARGET_DIR").unwrap_or_else(|_| "target".into());
    let patterns = [
        format!("{}/debug/build/rust-toy-app-*/out/rust-toy-app.1", target),
        format!("{}/release/build/rust-toy-app-*/out/rust-toy-app.1", target),
    ];

    let found = patterns.iter().any(|pat| {
        glob(pat)
            .expect("valid glob")
            .any(|entry| entry.map(|p| p.exists()).unwrap_or(false))
    });
    assert!(found, "man page not found; searched: {patterns:?}");
}


