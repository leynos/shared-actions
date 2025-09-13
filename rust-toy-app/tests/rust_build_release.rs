//! Integration test: ensure the composite action's Python entrypoint builds a release binary and man page.

use assert_cmd::prelude::*;
use glob::glob;
use std::path::PathBuf;
use std::process::Command;

#[test]
fn builds_release_binary_and_manpage() {
    let project_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let script = project_dir
        .parent()
        .unwrap()
        .join(".github/actions/rust-build-release/src/main.py");

    Command::new(script)
        .arg("x86_64-unknown-linux-gnu")
        .current_dir(&project_dir)
        .assert()
        .success();

    assert!(project_dir
        .join("target/x86_64-unknown-linux-gnu/release/rust-toy-app")
        .exists());
    let pattern = project_dir.join(
        "target/x86_64-unknown-linux-gnu/release/build/rust-toy-app-*/out/rust-toy-app.1",
    );
    assert!(glob(pattern.to_str().unwrap()).unwrap().next().is_some());
}
