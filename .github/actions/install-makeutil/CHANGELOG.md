# Changelog

All notable changes to the `install-makeutil` action will be documented in this
file.

## Unreleased

- Pin makeutil 0.1.1 (digests taken from the release's `.sha256` files) and make
  it the default `version`. 0.1.0 stays in the table for callers that name it.
- Add a composite action that installs makeutil's prebuilt static Linux
  binary, verified against a pinned digest table and the release's own
  `.sha256` sidecar, and never builds from source.
- Support `x86_64-unknown-linux-musl` and `aarch64-unknown-linux-musl`;
  reject any other runner OS/architecture with
  `install-makeutil.result=unsupported-platform` before the digest table is
  consulted.
- Pin a digest table in `scripts/makeutil_plan.py`, keyed by (version,
  target); reject a version missing from the table with
  `install-makeutil.result=unknown-version` rather than installing an
  unverified asset. There is no floating or `latest` version.
- Verify both the pinned table digest and the release's published sidecar
  before writing anything to `bin-dir`; install to a staged file and move it
  into place only once both checks pass.
- Cache the installed executable, keyed on
  `install-makeutil-<version>-<target>-<digest>`. A cache hit is re-verified
  against the pinned digest and replaced on a mismatch.
- Refuse any redirect hop that is not HTTPS, so a release asset cannot be
  bounced to a cleartext or non-HTTP origin.
- Emit an `install-makeutil.cache` line over `hit`, `miss` and `stale`, fed by
  the cache step's `cache-hit` output, so a restored-but-rejected entry is told
  apart from an ordinary miss.
- Set the `result` output on every terminal path, refusals and failures
  included, so a caller using `continue-on-error` can assert why a run failed;
  `path` and `version` stay success-only.
- Put the installed binary behind a `BinaryStore` port with a
  `FilesystemBinaryStore` adapter that maps `OSError` to `StoreError`, and
  report a failure writing step outputs instead of raising it.
- Split `resolve` into a side-effect-free `resolve_plan` query returning a
  typed plan, and a command that publishes it; the home directory is injected
  and an unresolvable `bin-dir` is `invalid-input`.
- Make `resolve` a pure query: it no longer creates `bin-dir`, which the
  `install` step now creates, reporting a failure as `install-failed`.
- Add a test-only `expected-sha256-override` input, so a workflow can
  tamper with the expected digest and assert that a genuinely correct download
  is still refused.
- Emit exactly one `install-makeutil.result` line per run, over a bounded
  vocabulary: `invalid-input`, `unsupported-platform`, `unknown-version`,
  `cached`, `installed`, `digest-mismatch`, `sidecar-mismatch`,
  `download-failed`, `install-failed`.
