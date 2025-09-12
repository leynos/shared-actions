use assert_cmd::prelude::*;
use glob::glob;
use std::path::PathBuf;
use std::process::Command;

#[test]
fn action_builds_release_binary_and_manpage() {
    let script = PathBuf::from("../.github/actions/rust-build-release/src/main.py");
    Command::new(&script)
        .env("RBR_TARGET", "x86_64-unknown-linux-gnu")
        .assert()
        .success();

    let binary = PathBuf::from("target/x86_64-unknown-linux-gnu/release/rust-toy-app");
    assert!(binary.exists());

    let pattern = "target/x86_64-unknown-linux-gnu/release/build/rust-toy-app-*/out/rust-toy-app.1";
    let found = glob(pattern)
        .expect("valid glob")
        .any(|entry| entry.map(|p| p.exists()).unwrap_or(false));
    assert!(found, "man page missing at {}", pattern);
}
