//! Build-script support for generating CLI man pages from the shared CLI module.

use std::env;

use std::path::PathBuf;
use time::OffsetDateTime;

// The build script only needs `cli::command()`; the rest of the module is
// exercised by the library crate, so unused items here are expected.
#[allow(dead_code)]
#[path = "src/cli.rs"]
mod cli;

#[path = "build_support/target_root.rs"]
mod target_root;

use target_root::derive_target_root;

/// Generates the `rust-toy-app.1` man page and writes it to the stable
/// `target/generated-man/<TARGET>/<PROFILE>/` directory so that the
/// release-staging action can locate it at a deterministic path.
fn main() -> std::io::Result<()> {
    // Rebuild when the CLI definition changes.
    println!("cargo:rerun-if-changed=src/cli.rs");
    println!("cargo:rerun-if-changed=Cargo.toml");
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-env-changed=SOURCE_DATE_EPOCH");
    let out_dir = env::var("OUT_DIR")
        .map(PathBuf::from)
        .map_err(std::io::Error::other)?;
    let target = env::var("TARGET").map_err(std::io::Error::other)?;
    let profile = env::var("PROFILE").map_err(std::io::Error::other)?;

    // Prefer the explicit target directory set by cross / CARGO_TARGET_DIR.
    // Fall back to deriving the root from OUT_DIR's ancestor structure.
    let target_root: PathBuf = match env::var_os("CARGO_TARGET_DIR") {
        Some(cargo_target_dir) => PathBuf::from(cargo_target_dir),
        None => derive_target_root(&out_dir, &target, &profile)?,
    };

    let man_dir = target_root
        .join("generated-man")
        .join(&target)
        .join(&profile);

    // Force rerun when cross changes CARGO_TARGET_DIR between runs.
    println!("cargo:rerun-if-env-changed=CARGO_TARGET_DIR");

    // Diagnostic: visible in cargo build output; confirms chosen stable path.
    println!("cargo:warning=writing man page to {}", man_dir.display());
    std::fs::create_dir_all(&man_dir)?;

    let cmd = cli::command();
    let man = clap_mangen::Man::new(cmd).date(build_date());
    let mut buffer = Vec::new();
    man.render(&mut buffer)?;
    std::fs::write(man_dir.join("rust-toy-app.1"), &buffer)?;
    Ok(())
}

/// Returns the man-page date string.
///
/// Reads `SOURCE_DATE_EPOCH` (a Unix timestamp) and formats it as
/// `YYYY-MM-DD`. Falls back to `"1970-01-01"` when the variable is absent or
/// unparsable, ensuring reproducible builds.
fn build_date() -> String {
    env::var("SOURCE_DATE_EPOCH")
        .ok()
        .and_then(|s| s.parse::<i64>().ok())
        .and_then(|e| OffsetDateTime::from_unix_timestamp(e).ok())
        .map(|dt| dt.date().to_string())
        .unwrap_or_else(|| "1970-01-01".to_string())
}
