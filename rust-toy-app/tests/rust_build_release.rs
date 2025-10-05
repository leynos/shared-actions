//! Integration test: ensure the composite action's Python entrypoint builds a release binary and man page.

use assert_cmd::prelude::*;
use glob::glob;
use std::env;
use std::path::PathBuf;
use std::process::Command;

mod runtime;
use runtime::runtime_available;

const TARGETS: &[&str] = &["x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu"];

#[test]
fn builds_release_binary_and_manpage() {
    let project_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let script = project_dir
        .parent()
        .unwrap()
        .join(".github/actions/rust-build-release/src/main.py");
    let action_dir = script.parent().expect("action directory");
    unsafe {
        env::set_var("GITHUB_ACTION_PATH", action_dir);
    }

    for target in TARGETS {
        if *target != "x86_64-unknown-linux-gnu" {
            let docker_available = runtime_available("docker");
            let podman_available = runtime_available("podman");
            if !docker_available && !podman_available {
                eprintln!("skipping {target} (container runtime required)");
                continue;
            }
        }

        Command::new(&script)
            .arg(target)
            .current_dir(&project_dir)
            .assert()
            .success();

        assert!(project_dir
            .join(format!("target/{target}/release/rust-toy-app"))
            .exists());
        let pattern = project_dir.join(format!(
            "target/{target}/release/build/rust-toy-app-*/out/rust-toy-app.1"
        ));
        assert!(glob(pattern.to_str().unwrap()).unwrap().next().is_some());
    }
}
