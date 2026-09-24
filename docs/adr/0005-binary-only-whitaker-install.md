# ADR 0005: install-whitaker installs binaries only

**Status:** Accepted **Date:** 2026-09-24

## Context

`install-whitaker` pinned the `whitaker-installer` binary thoroughly and left
the rest to the installer. When a published lint library or Dylint tool archive
was missing, the installer compiled it from source and exited zero. The action
tried to catch that afterwards by reading the installer's stdout for a fallback
notice, and only in `ci-mode`.

That check could not see the case that mattered most. The installer reports a
Dylint tool's fallback on stderr. On the Windows images, where the installer's
tool directory (`~/.local/bin`) is not on `PATH`, its post-install check of
cargo-dylint (`cargo dylint --version`) failed on every run, and every run
compiled cargo-dylint from source while the job stayed green. Main's Windows
leg in run 35999424438 logs "Installed cargo-dylint from source with cargo
install."

The action also offered `suite-version`, a pin on the lint suite, with
`allow-suite-pin` as its escape hatch in `ci-mode`. Whitaker's lints are a
rolling release, and prebuilt lint libraries exist only for its branch tip, so
a pin is also a source build.

The estate rule of 2026-09-24 settles all three: the lint suite is never
pinned, the installer binary is pinned to an exact version, and every consumer
passes `--no-source-fallback`, so a missing published artefact fails the run.
Whitaker's installer gained that flag in 0.2.9.

## Decision

`install-whitaker` carries the rule, so a consumer satisfies it by using the
action:

- The installer always receives `--no-source-fallback`, whatever `ci-mode`
  says.
- `installer-version` defaults to 0.2.9 and validation refuses anything older,
  since an older installer rejects the flag. The digest manifest pins 0.2.9
  only.
- A non-empty `suite-version` is refused. The input stays declared so that
  setting it fails the step; GitHub drops an undeclared input with a warning,
  which would let a pin look honoured. `allow-suite-pin` is removed.
- The run step puts the installer's tool directory on `PATH`, and on
  `GITHUB_PATH` for later steps, so the installer's own check of the tools it
  extracted can succeed on every platform.
- The source-build backstop reads stdout and stderr, and fails in every mode.
- `ci-mode` now only chooses whether the rolling-release assets are checked
  before the installer runs.
- A `cranelift` input forwards `--cranelift`, because the action is the only
  permitted route for repositories that previously ran the installer directly
  to add `rustc-codegen-cranelift`.

## Consequences

A run that would have compiled a Whitaker tool now fails, with the installer's
own message naming the artefact. That is the intent; a Windows lane that passed
only by compiling cargo-dylint now passes by using the published binary.

Consumers that pass `installer-version` below 0.2.9, `suite-version`, or
`allow-suite-pin` must drop them when they repin. The migration guide lists the
changes, and concordat's `whitaker-provisioning` rule refuses every other route
to Whitaker estate-wide.

The installer's `PATH`-dependent verification is tracked upstream as
leynos/whitaker#460. The action's `PATH` handling works around it and stays
correct once the installer verifies by explicit path.
