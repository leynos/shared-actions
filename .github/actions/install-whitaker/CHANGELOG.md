# Changelog

All notable changes to the `install-whitaker` action will be documented in this
file.

## v1.0.0 (Unreleased)

- Refuse a silent source build. Whitaker republishes its rolling release on
  every merge, and the publish briefly left the tag without a complete asset
  set. A consumer landed in it: chutoro's install began in the same second a
  republish deleted the release, could not fetch
  `cargo-dylint-x86_64-unknown-linux-gnu-v6.0.1.tgz`, and built the Dylint
  tools from source instead. That build succeeded, so the run looked healthy
  while testing something else, more slowly.

  A new `ci-mode` input, on by default, checks the rolling assets this target
  needs before the installer runs, retrying five times over about thirty
  seconds because a republish takes six or seven, and failing with the URL if
  they are still absent. Afterwards it reads the installer's own output and
  records `whitaker-installer.suite-source=<prebuilt|source>`, failing the step
  on `source`. The resolved nightly is recorded as
  `whitaker-installer.suite-toolchain=<toolchain>`.

  `ci-mode` also rejects a non-empty `suite-version`, because a pin forces the
  source build the mode exists to prevent, unless `allow-suite-pin: true` makes
  that deliberate. Set `ci-mode: false` for local reproduction, where a source
  build is a legitimate choice.

- Expose `suite-version` and pass it to the installer, so a caller can pin the
  lint suite instead of taking whatever is at the Whitaker default branch tip.
  This action pinned the installer thoroughly and the lints not at all, and a
  caller reading its inputs would reasonably have believed otherwise:
  `pg-embed-setup-unpriv`'s lint gate was red from 2026-08-19 to 2026-09-04
  through exactly that, with the same installer version, the same toolchain and
  a different suite commit.

  A pin costs a source build, since prebuilt lint libraries are published only
  for the tip, and requires installer 0.2.8 or later. The default is unchanged,
  so nothing gets slower without asking. Each run reports
  `whitaker-installer.suite=<pinned-commit|pinned-mutable-ref|default-branch-tip>`,
  which makes an unpinned lane's exposure visible rather than silent. The
  pinned arm is split by mutability: a branch or a tag can advance without the
  caller changing a line, so reporting either as simply pinned would claim a
  protection the lane does not have.

  The default `installer-version` moves to 0.2.8, whose digests are pinned for
  all five targets, each computed from an independent download and cross-checked
  against the release sidecar.

- Run the action's Bash fragments from a file in the test harness, as a
  runner does, instead of passing them to `bash -c`. The two are not the same
  shell invocation: Bash 3.2, which macOS runners ship, replaces itself with
  the last command of a `bash -c` string when that command is external,
  discarding any `ERR` trap along with the shell. No fragment here ends in a
  bare external command today, so no assertion was wrong, but nothing kept it
  that way. The action's own behaviour is unchanged.
- Drop this action's private copy of the fragment harness in favour of the
  shared `composite_fragments` module, which carries that fix and two tests for
  it.

- Fix extraction of the Windows release asset on GitHub-hosted runners
  (#446). The extract step chose its extractor by probing what `tar` is, and on
  `windows-latest` the answer is GNU tar, which cannot read the `.zip` asset:
  the step's `shell: bash` is Git Bash, whose PATH puts MSYS2's tar ahead of
  the Windows system directory. The probe held only on runner images where
  `tar` happened to resolve to bsdtar. The extractor is now chosen by the
  resolved asset extension instead. A tarball still goes through `tar`, with the
  `--force-local` probe unchanged; a zip goes through the Windows system
  `tar.exe`, which is bsdtar and honours `--strip-components`, falling back to
  a Python extractor the action ships for a runner that has neither. An
  unrecognized extension now fails closed rather than falling through to `tar`.
  The action still never requires `unzip`, which some Windows images lack.

  The unit tests' `tar` shim previously unpacked zip archives itself, which
  made the harness's tar zip-capable and hid this for as long as it existed. It
  now refuses a zip exactly as GNU tar does, so the zip arm has to work for the
  Windows scenario to pass, and the `Test install-whitaker` workflow gained a
  `windows-latest` leg, which is the only place the real PATH ordering can be
  observed.

- Fix extraction on Windows runners. `RUNNER_TEMP` is a native path, so under
  Git Bash the staging directory arrives as `D:\a\_temp/...`, and GNU tar reads
  the colon as rmt's `host:path` syntax and tries to resolve the drive letter
  as a hostname. The staging path is now normalized to POSIX form with
  `cygpath -u` where it is first produced, so every later step receives it, and
  GNU tar is additionally passed `--force-local`. bsdtar, the bundled tar on
  Windows and macOS runners, rejects that flag, so it is only passed after a
  version probe identifies GNU tar.

- Fix digest verification on Windows runners. `sha256sum` escapes its output
  line when the file name contains a backslash or a newline, prefixing the line
  with a backslash, and Windows paths contain backslashes, so the verify step
  read `\<digest>` and rejected a correct archive. Digests are now computed
  from standard input, which keeps the file name out of the output entirely,
  and a digest read from a release sidecar has any leading backslash stripped.

- Add cached installation of the Whitaker Dylint suite
- Download and verify official prebuilt installer releases without a source
  build fallback
- Support caller-owned caches through `cache-provider: external`
- Cache the installed suite as well as the installer, keyed by `dylint.toml`
- Verify the installer archive against digests pinned in the action rather than
  the release's own `.sha256` sidecar, which a compromised release could forge
- Add the optional `installer-sha256` input for an asset the pinned manifest
  does not cover, and fail closed when neither anchor is available
- Give the pinned manifest precedence over `installer-sha256`, rejecting a
  supplied digest that disagrees with a pinned one
- Split the release lifecycle into explicit resolve, download, verify, extract,
  and install steps
- Extract both archive formats with `tar` rather than `unzip`, which is not
  present on every runner image
- Retry each release download with bounded connect and transfer timeouts, and
  record each transfer's outcome, HTTP status, size, duration, and attempt count
- Separate release resolution, which is now a pure query in
  `scripts/resolve-release.sh`, from the adapter step that captures its record
  and the publication step that writes outputs, metrics, and annotations
- Lower-case a supplied `installer-sha256` with `tr`, since macOS runners ship
  Bash 3.2, which has no `${var,,}` expansion
- Report an unsupported runner as a resolution record rather than a non-zero
  return, which `errtrace` turned into a spurious internal failure
- Reject an unsupported runner before considering a cached installer, so a
  cached installer cannot report success for a platform this action cannot
  install
- Propagate a failed read of the digest manifest or the version marker instead
  of treating it as an absent file, which would have fallen back to the
  caller's digest or reused an installer of unknown version
- Record the installed version beside the installer and reinstall when a cached
  installer was built for another version, which a caller-owned Cargo home
  would otherwise reuse indefinitely
- Report the digest outcome and trust-anchor source in the job summary
- Default `installer-version` to `0.2.7`
