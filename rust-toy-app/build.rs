//! Build-script support for generating CLI man pages from the shared CLI module.

use std::env;

use std::path::PathBuf;
use time::OffsetDateTime;

#[expect(
    dead_code,
    reason = "the build script includes the CLI module only to render clap metadata"
)]
#[path = "src/cli.rs"]
mod cli;

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
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
    let target =
        env::var("TARGET").map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
    let profile =
        env::var("PROFILE").map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;

    let profile_dir = out_dir.ancestors().nth(3).ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::Other,
            format!("unexpected OUT_DIR structure: {}", out_dir.display()),
        )
    })?;
    if profile_dir.file_name().and_then(|name| name.to_str()) != Some(profile.as_str()) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            format!("unexpected OUT_DIR profile: {}", out_dir.display()),
        ));
    }
    let profile_parent = profile_dir.parent().ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::Other,
            format!("unexpected OUT_DIR structure: {}", out_dir.display()),
        )
    })?;
    let target_root =
        if profile_parent.file_name().and_then(|name| name.to_str()) == Some(target.as_str()) {
            profile_parent.parent().ok_or_else(|| {
                std::io::Error::new(
                    std::io::ErrorKind::Other,
                    format!("unexpected OUT_DIR structure: {}", out_dir.display()),
                )
            })?
        } else {
            profile_parent
        };

    let man_dir = target_root
        .join("generated-man")
        .join(&target)
        .join(&profile);
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
/// unparseable, ensuring reproducible builds.
fn build_date() -> String {
    env::var("SOURCE_DATE_EPOCH")
        .ok()
        .and_then(|s| s.parse::<i64>().ok())
        .and_then(|e| OffsetDateTime::from_unix_timestamp(e).ok())
        .map(|dt| dt.date().to_string())
        .unwrap_or_else(|| "1970-01-01".to_string())
}
