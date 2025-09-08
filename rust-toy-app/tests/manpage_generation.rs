use assert_cmd::prelude::*;
use std::process::Command;
use glob::glob;

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
        .success();

    let target = std::env::var("CARGO_TARGET_DIR").unwrap_or_else(|_| "target".into());
    let patterns = [
        format!("{}/debug/build/rust-toy-app-*/out/*.1", target),
        format!("{}/release/build/rust-toy-app-*/out/*.1", target),
    ];

    let mut found = false;
    for pat in &patterns {
        for entry in glob(pat).expect("valid glob") {
            if let Ok(path) = entry {
                if path.exists() {
                    found = true;
                    break;
                }
            }
        }
        if found { break; }
    }
    assert!(found, "man page not found; searched: {patterns:?}");
}
