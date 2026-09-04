# Changelog

All notable changes to the `install-mdtablefix` action will be documented in
this file.

## Unreleased

- Support every platform mdtablefix 0.5.1 publishes, and require 0.5.1 or
  later. The platform gate admitted Linux `x86_64` and `aarch64` only, which
  was correct for 0.5.0 and left consumers with a macOS or Windows formatter
  lane carrying their own source-build exception. 0.5.1 publishes Linux and
  macOS on both architectures and Windows on `x86_64`
  (leynos/mdtablefix#459), so those consumers can now retire the exception.
  Windows on `aarch64` still fails closed, because no such archive exists; a
  FreeBSD archive is published but has no entry, because GitHub offers no
  FreeBSD runner label to gate on.

- Accept a native Windows `bin-dir`. `${{ runner.temp }}/...` is the natural
  value for a Windows caller and arrives as `D:\a\_temp\bin`, which Git Bash
  does not treat as absolute; the validator converts it with `cygpath` instead
  of rejecting the one expression such a caller would reach for.

- Drop the `--bin-dir` override. 0.5.0 declared `bin-dir = "."`, which
  cargo-binstall rejects, so the install passed an override naming the
  executable itself (leynos/mdtablefix#458). 0.5.1 carries correct metadata and
  the new version floor refuses anything earlier, so the override would now
  second-guess a manifest the crate owns. A test asserts it has not returned.

- Default `version` to 0.5.1 rather than requiring it. The action supports one
  metadata shape and one platform list, so there is a single sensible value and
  making every caller restate it only invites drift.

- Pass the caller's `github.token` to the install step. Resolving the release
  asset costs one `api.github.com` call, and unauthenticated calls are
  rate-limited per source address; the macOS runners share addresses widely
  enough to return `403 Forbidden`. A job with no token still works, just
  unauthenticated, exactly as before.

- Widen the runner-backed workflow to a matrix of `ubuntu-latest`, `macos-15`
  and `windows-latest`, each asserting the installed version and the cached
  second call. The macOS job that asserted a fail-closed refusal now asserts a
  successful install, and `windows-11-arm` takes over the fail-closed case,
  which is the only platform where the refusal is still about a genuinely
  absent asset.
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
- Report a failed provisioning of cargo-binstall as
  `install-mdtablefix.result=binstall-unavailable`. The upstream installer is
  the one step this action cannot annotate from inside, so a
  `failure()`-guarded step follows it; without that, a bad `binstall-version`
  would stop the action with no result metric at all.
- Report a refused input as `install-mdtablefix.result=invalid-input`, and
  refuse a `bin-dir` the runner cannot create or enter the same way, so no
  terminal path leaves the run without exactly one outcome.
- Read at most the first line, truncated, of whatever `--version` prints. What
  sits in `bin-dir` came from the caller's cache, so its output is neither
  trusted nor copied into an annotation unbounded.
- Name leynos/mdtablefix#459 in the `no-prebuilt` annotation and in the
  guides, so a run that fails closed says what would unblock its platform.
- Keep every fragment within Bash 3.2, which is what macOS runners ship.
- Report a failed install by checking `cargo binstall`'s exit status rather
  than from an `ERR` trap, which is what the sibling installers use. Both work
  on a runner; a checked status is also invariant to how the fragment is
  invoked, which a trap is not.
