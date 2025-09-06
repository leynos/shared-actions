//! Integration tests for the `rust-toy-app` binary CLI.
use assert_cmd::prelude::*;
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
