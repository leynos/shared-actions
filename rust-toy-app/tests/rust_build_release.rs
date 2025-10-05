//! Integration test: ensure the composite action's Python entrypoint builds a release binary and man page.

mod common;

use assert_cmd::prelude::*;
use common::assert_manpage_exists_in;
use std::path::PathBuf;
use std::process::Command;

mod runtime;
mod test_helpers;
use runtime::runtime_available;
use test_helpers::EnvGuard;

const TARGETS: &[&str] = &["x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu"];

#[test]
fn builds_release_binary_and_manpage() {
    let project_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let script = project_dir
        .parent()
        .unwrap()
        .join(".github/actions/rust-build-release/src/main.py");
    let action_dir = script.parent().expect("action directory");
    let _env_guard = EnvGuard::set(
        "GITHUB_ACTION_PATH",
        action_dir.to_str().expect("valid UTF-8 path"),
    );

    for target in TARGETS {
        if *target != "x86_64-unknown-linux-gnu" {
            let docker_available = runtime_available("docker");
            let podman_available = runtime_available("podman");
            if !docker_available && !podman_available {
                eprintln!("skipping {} (container runtime required)", target);
                continue;
            }
        }

        Command::new(&script)
            .arg(target)
            .env("GITHUB_ACTION_PATH", action_dir)
            .current_dir(&project_dir)
            .assert()
            .success();

        assert!(project_dir
            .join(format!("target/{target}/release/rust-toy-app"))
            .exists());
        let target_root = project_dir.join(format!("target/{target}"));
        assert_manpage_exists_in(&target_root);
    }
}
