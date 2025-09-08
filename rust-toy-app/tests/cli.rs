//! Integration tests for the `rust-toy-app` binary CLI.
use assert_cmd::prelude::*;
use predicates::prelude::*;
use std::process::Command;
use rstest::rstest;

mod common;
use common::assert_manpage_exists;

#[rstest]
#[case::named(&["--name", "Bob"][..], "Hello, Bob!\n")]
#[case::default(&[][..], "Hello, world!\n")]
fn prints_greeting(#[case] args: &[&str], #[case] expected: &'static str) {
    let mut cmd = Command::cargo_bin("rust-toy-app").expect("binary should build");
    cmd.args(args);
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
    let _ = Command::cargo_bin("rust-toy-app").expect("binary should build");
    assert_manpage_exists();
}


