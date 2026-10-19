# Changelog

## v1.0.0 (Unreleased)

- Prune interpreter-bound script environments restored by the calling workflow
  before installing Nixie through uv.
- Add pinned Nixie and Merman command-line interface (CLI) installation.
- Support the Windows/X64 Merman release and executable shims.
- Install Merman with `cargo-binstall`, using only prebuilt binaries and exact
  version requirements. The default `0.7.0` pin publishes binaries for every
  supported target; unsupported versions and platforms, or a version without a
  compatible prebuilt release, fail closed instead of building from source.
- Verify a Merman install only by running the executable and confirming that it
  reports the requested version; it is no longer matched against pinned Secure
  Hash Algorithm 256-bit (SHA-256) archive digests.
- Keep Nixie and Python overrides, reconciling normally on a warm cache and
  forcing a reinstallation only to repair a missing Nixie executable shim.
- Reuse Merman only from its version-scoped cache path after confirming that
  the cached executable runs and reports the requested version.
- Authenticate `cargo-binstall`'s release lookup with the job token, scoped to
  that one command so no other child process inherits it.
- Expose the Merman and Nixie executable directories to later steps.
- Fail before installing anything when `uv` or Cargo is absent, naming the
  missing prerequisite.
