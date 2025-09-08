mod common;

use assert_cmd::prelude::*;
use predicates::prelude::*;
use std::process::Command;
use common::assert_manpage_exists;

#[test]
fn generates_manpage() {
    // Skip if cargo mangen subcommand is unavailable.
    let available = Command::new("cargo")
        .args(["mangen", "--help"])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    if !available {
        eprintln!("cargo mangen not installed; skipping");
        return;
    }

    Command::new("cargo")
        .args(["mangen", "rust-toy-app"])
        .assert()
        .success()
        .stdout(predicates::str::contains("NAME").and(predicates::str::contains("rust-toy-app")));

    assert_manpage_exists();
}

