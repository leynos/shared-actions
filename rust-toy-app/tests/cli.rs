use assert_cmd::prelude::*;
use predicates::prelude::*;
use std::process::Command;

#[test]
fn prints_greeting() {
    let mut cmd = Command::cargo_bin("rust-toy-app").unwrap();
    cmd.arg("--name").arg("Bob");
    cmd.assert().success().stdout(predicate::str::contains("Hello, Bob!"));
}
