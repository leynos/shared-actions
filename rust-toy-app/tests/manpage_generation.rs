//! Checks that the build script writes the toy app's manual page.

mod common;

use assert_cmd::prelude::*;
use common::assert_manpage_exists;
use std::process::Command;

#[test]
fn generates_manpage() {
    // The toy app commits its Cargo.lock; `--frozen` keeps this nested build
    // from resolving or fetching while it holds the shared package-cache lock.
    Command::new("cargo")
        .args(["build", "--frozen"])
        .assert()
        .success();

    assert_manpage_exists();
}


