use assert_cmd::prelude::*;
use glob::glob;
use std::process::Command;

#[test]
fn manpage_generated() {
    Command::new("cargo")
        .arg("build")
        .current_dir(env!("CARGO_MANIFEST_DIR"))
        .assert()
        .success();
    assert!(
        glob("target/*/build/*/out/rust-toy-app.1")
            .unwrap()
            .any(|p| p.is_ok()),
        "man page not generated"
    );
}
