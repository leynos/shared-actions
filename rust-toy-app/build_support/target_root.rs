//! Derives the Cargo target root from a build script's `OUT_DIR`.
//!
//! Shared by `build.rs` and the `target_root` integration test, which cannot
//! otherwise exercise build-script code.

use std::path::{Path, PathBuf};

/// Derives the Cargo target root from `OUT_DIR`'s ancestor structure.
///
/// `OUT_DIR` is `<target-root>[/<target>]/<profile>/build/<pkg>/out` on older
/// Cargo releases and `<target-root>[/<target>]/<profile>/build/<pkg>/<hash>/out`
/// on newer ones. Rather than counting levels, this walks up to the first
/// directory named `build` whose parent is named `profile`, then strips the
/// optional target triple component. Only a `build` directory that sits exactly
/// two or three components above `out_dir` qualifies, so an unrelated
/// `<profile>/build` higher in the path is never mistaken for the real one.
///
/// # Errors
///
/// Returns an error when no `build` directory under a `profile` directory is
/// found among the ancestors of `out_dir`.
pub fn derive_target_root(out_dir: &Path, target: &str, profile: &str) -> std::io::Result<PathBuf> {
    let structure_error = || {
        std::io::Error::other(format!(
            "unexpected OUT_DIR structure: {}",
            out_dir.display()
        ))
    };
    let name_is = |path: &Path, expected: &str| {
        path.file_name().and_then(|name| name.to_str()) == Some(expected)
    };
    let profile_dir = out_dir
        .ancestors()
        .find(|dir| {
            name_is(dir, "build")
                && dir.parent().is_some_and(|parent| name_is(parent, profile))
                && out_dir
                    .strip_prefix(dir)
                    .is_ok_and(|suffix| matches!(suffix.components().count(), 2 | 3))
        })
        .and_then(Path::parent)
        .ok_or_else(|| {
            std::io::Error::other(format!("unexpected OUT_DIR profile: {}", out_dir.display()))
        })?;
    let profile_parent = profile_dir.parent().ok_or_else(structure_error)?;
    if name_is(profile_parent, target) {
        Ok(profile_parent
            .parent()
            .ok_or_else(structure_error)?
            .to_path_buf())
    } else {
        Ok(profile_parent.to_path_buf())
    }
}
