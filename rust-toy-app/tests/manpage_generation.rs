//! Checks that the build script writes the toy app's manual page.

mod common;

use assert_cmd::prelude::*;
use common::assert_manpage_exists;
use std::process::Command;

#[test]
fn generates_manpage() {
    Command::new("cargo")
        .arg("build")
        .assert()
        .success();

    assert_manpage_exists();
}


