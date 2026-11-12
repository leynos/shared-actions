//! Tests for the build script's `OUT_DIR` target-root derivation across the
//! Cargo layouts that place a hash level under `build/<pkg>/` or omit it.

#[path = "../build_support/target_root.rs"]
mod target_root;

use std::path::{Path, PathBuf};

use rstest::rstest;
use target_root::derive_target_root;

const TARGET: &str = "x86_64-unknown-linux-gnu";

#[rstest]
#[case::legacy_host("/w/target/debug/build/pkg/out", "debug", "/w/target")]
#[case::hashed_host("/w/target/debug/build/pkg/0123abcd/out", "debug", "/w/target")]
#[case::legacy_triple(
    "/w/target/x86_64-unknown-linux-gnu/release/build/pkg/out",
    "release",
    "/w/target"
)]
#[case::hashed_triple(
    "/w/target/x86_64-unknown-linux-gnu/release/build/pkg/0123abcd/out",
    "release",
    "/w/target"
)]
fn derives_the_target_root(#[case] out_dir: &str, #[case] profile: &str, #[case] expected: &str) {
    let root = derive_target_root(Path::new(out_dir), TARGET, profile).expect("layout matches");
    assert_eq!(root, PathBuf::from(expected));
}

#[rstest]
#[case::wrong_profile("/w/target/debug/build/pkg/out", "release")]
#[case::no_build_dir("/w/target/debug/pkg/out", "debug")]
#[case::unrelated_outer_build("/w/debug/build/project/target/ci/build/pkg/0123abcd/out", "debug")]
fn rejects_unrecognised_layouts(#[case] out_dir: &str, #[case] profile: &str) {
    let error =
        derive_target_root(Path::new(out_dir), TARGET, profile).expect_err("layout does not match");
    assert!(error.to_string().contains("unexpected OUT_DIR"));
}
