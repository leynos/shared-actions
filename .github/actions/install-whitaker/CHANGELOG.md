# Changelog

All notable changes to the `install-whitaker` action will be documented in
this file.

## v1.0.0 (Unreleased)

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
  from standard input, which keeps the file name out of the output entirely, and
  a digest read from a release sidecar has any leading backslash stripped.

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
  record each transfer's outcome, HTTP status, size, duration, and attempt
  count
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
