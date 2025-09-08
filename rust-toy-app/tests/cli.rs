//! Integration tests for the `rust-toy-app` binary CLI.
use assert_cmd::prelude::*;
use predicates::prelude::*;
use std::process::Command;
use rstest::rstest;

#[rstest]
#[case::named(&["--name", "Bob"][..], "Hello, Bob!\n")]
#[case::default(&[][..], "Hello, world!\n")]
fn prints_greeting(#[case] args: &[&str], #[case] expected: &'static str) {
    let mut cmd = Command::cargo_bin("rust-toy-app").expect("binary should build");
    for a in args {
        cmd.arg(a);
    }
    cmd.assert().success().stdout(expected);
}

#[test]
fn shows_help() {
    let mut cmd = Command::cargo_bin("rust-toy-app").expect("binary should build");
    cmd.arg("--help")
        .assert()
        .success()
        .stdout(predicate::str::contains("Usage"));
}

#[test]
fn unknown_flag_errors() {
    let mut cmd = Command::cargo_bin("rust-toy-app").expect("binary should build");
    cmd.arg("--nope")
        .assert()
        .failure().code(2)
        .stderr(predicate::str::contains("error:"));
}

#[test]
fn missing_name_errors() {
    let mut cmd = Command::cargo_bin("rust-toy-app").expect("binary should build");
    cmd.arg("--name")
        .assert()
        .failure().code(2)
        .stderr(predicate::str::contains("requires a value").or(predicate::str::contains("value is required")));
}

#[test]
fn builds_manpage_into_out_dir() {
    // Build the binary (ensures build.rs runs).
    let _ = Command::cargo_bin("rust-toy-app").expect("binary should build");

    let target = std::env::var("CARGO_TARGET_DIR").unwrap_or_else(|_| "target".into());
    let patterns = [
        format!("{}/debug/build/rust-toy-app-*/out/rust-toy-app.1", target),
        format!("{}/release/build/rust-toy-app-*/out/rust-toy-app.1", target),
    ];

    let mut found = false;
    for pat in &patterns {
        for entry in glob::glob(pat).expect("valid glob") {
            if let Ok(path) = entry {
                if path.exists() {
                    found = true;
                    break;
                }
            }
        }
        if found { break; }
    }
    assert!(found, "man page not found; searched: {patterns:?}");
}


