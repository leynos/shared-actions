mod common;

use assert_cmd::prelude::*;
use std::process::Command;
use common::assert_manpage_exists;

#[test]
fn generates_manpage() {
    Command::new("cargo")
        .arg("build")
        .assert()
        .success();
    assert_manpage_exists();
}

