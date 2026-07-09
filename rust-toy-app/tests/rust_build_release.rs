//! Integration test: ensure the composite action's Python entrypoint builds a release binary and man page.

mod common;

use assert_cmd::prelude::*;
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
        // build.rs writes the man page to the deterministic path
        // target/generated-man/<target>/release, so assert the exact file for
        // the current target rather than globbing the whole target root;
        // otherwise an artefact left by an earlier target or a debug build
        // would satisfy the assertion.
        let manpage = project_dir.join(format!(
            "target/generated-man/{target}/release/rust-toy-app.1"
        ));
        assert!(
            manpage.exists(),
            "man page for {target} not found at {}",
            manpage.display()
        );
    }
}
