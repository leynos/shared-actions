# Changelog

All notable changes to the `install-mdtablefix` action will be documented in
this file.

## Unreleased

- Add a composite action that installs a pinned `mdtablefix` from its prebuilt
  release, replacing the per-repository installer steps that had diverged.
- Probe `bin-dir` and `PATH` first and exit early with
  `install-mdtablefix.result=cached` when the pinned version is already
  present. The action caches nothing itself, so the caller keeps sole ownership
  of the `bin-dir` cache key.
- Probe `cargo-binstall` by running `cargo binstall -V` rather than by looking
  for a name on `PATH`, which reports an unusable shim as present (issue #420).
  When the probe fails, install it with
  `cargo-bins/cargo-binstall@75b4bfae1b2c753a6806bbce6e6cb89b602de33c`
  (v1.22.0), pinned by commit SHA and selected by an `if:` over the probe's
  output.
- Harden the install:
  `--no-confirm --locked --disable-strategies compile --disable-telemetry`, so
  a missing prebuilt asset fails closed instead of compiling in CI.
- Pass `--bin-dir '{ bin }{ binary-ext }'` to neutralize `mdtablefix` 0.5.0's
  `bin-dir = "."` metadata, which cargo-binstall 1.22 rejects with "bin-dir
  configuration provided generates empty source path" (leynos/mdtablefix#458).
  Remove the override, and the test that pins it, once a pinned release carries
  fixed metadata.
- Verify the installed executable's reported version and fail with
  `install-mdtablefix.result=version-mismatch` when it is not the pinned one.
- Reject a runner with no prebuilt release with
  `install-mdtablefix.result=no-prebuilt`, before the cache is consulted, so a
  cached executable cannot report success on a platform the action could not
  have installed. `mdtablefix` 0.5.0 publishes archives only for Linux gnu on
  `x86_64` and `aarch64`, so macOS and Windows both fail closed.
- Validate every input before use, rejecting a version that is not three
  numeric components and a `bin-dir` that is relative, over-long, contains a
  newline, a parent-directory component, or the runner's `PATH` separator.
- Keep every fragment within Bash 3.2, which is what macOS runners ship.
- Report a failed install by checking `cargo binstall`'s exit status rather
  than from an `ERR` trap. Bash 3.2, which macOS runners ship, did not run the
  trap: the step failed with no `::error` annotation and no metric. A manifest
  test now rejects an `ERR` trap in any fragment.
