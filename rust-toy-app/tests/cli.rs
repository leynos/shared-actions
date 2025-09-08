//! Integration tests for the `rust-toy-app` binary CLI.
use assert_cmd::prelude::*;
use predicates::prelude::*;
use std::process::Command;
use rstest::{fixture, rstest};

mod common;
use common::assert_manpage_exists;

#[fixture]
fn bin_cmd() -> Command {
    Command::cargo_bin("rust-toy-app").expect("binary should build")
}

#[rstest]
#[case::named(&["--name", "Bob"][..], "Hello, Bob!\n")]
#[case::default(&[][..], "Hello, world!\n")]
fn prints_greeting(#[case] args: &[&str], #[case] expected: &'static str) {
    let mut cmd = bin_cmd();
    cmd.args(args);
    cmd.assert().success().stdout(expected);
}

#[rstest]
#[case("--help")]
#[case("-h")]
fn shows_help(#[case] flag: &str) {
    let mut cmd = bin_cmd();
    cmd.arg(flag)
        .assert()
        .success()
        .code(0)
        .stdout(predicate::str::contains("Usage"));
}

#[test]
fn unknown_flag_errors() {
    let mut cmd = bin_cmd();
    cmd.arg("--nope")
        .assert()
        .failure()
        .code(2)
        .stderr(
            predicate::str::contains("error:")
                .and(predicate::str::contains("--nope")),
        );
}

#[test]
fn missing_name_errors() {
    let mut cmd = bin_cmd();
    cmd.arg("--name")
        .assert()
        .failure().code(2)
        .stderr(predicate::str::contains("requires a value").or(predicate::str::contains("value is required")));
}

#[test]
fn builds_manpage_into_out_dir() {
    let _ = bin_cmd();
    assert_manpage_exists();
}


