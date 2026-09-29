# Install makeutil

Install makeutil's prebuilt static Linux binary, verified twice, never from
source.

The action resolves the runner's target triple, looks up a SHA-256 digest this
action pins for the requested version, downloads the release asset and its
published `.sha256` sidecar, and installs the binary only when both digests
agree with the downloaded bytes. Nothing is written to `bin-dir` until both
checks pass.

## Platforms

This action installs `x86_64-unknown-linux-musl` and
`aarch64-unknown-linux-musl` static binaries. It supports Linux only.

| Runner            | Outcome                                     |
| ----------------- | ------------------------------------------- |
| `Linux` / `X64`   | Installed from the prebuilt binary          |
| `Linux` / `ARM64` | Installed from the prebuilt binary          |
| anything else     | Fails closed, `result=unsupported-platform` |

The platform is rejected before the digest table is consulted, and the version
is rejected before anything is downloaded, so a run that fails closed never
leaves a partial or unverified binary behind.

## Version pinning

There is no floating or `latest` version. `version` must name three numeric
components, and the action's digest table (`scripts/install_makeutil.py`) must
already carry the SHA-256 digest for that version on the runner's target; a
version missing from the table fails with `result=unknown-version` rather than
installing an unverified asset.

## Verification

Both checks are required, and both fail closed:

- the downloaded binary's SHA-256 must equal the entry this action pins for
  the version and target;
- it must also equal the digest published in the release's own
  `makeutil-<target>.sha256` sidecar.

A mismatch with either check, an unreadable or malformed sidecar, or a sidecar
naming a different file, installs nothing: the binary is written to a staged
file beside `bin-dir` and moved into place only after both checks pass.

## Caching

Unlike `install-mdtablefix`, this action owns its own cache: the key folds in
the pinned digest (`install-makeutil-<version>-<target>-<digest>`), which only
this action knows, so only this action can keep the key correct as the digest
table changes.

A cache hit is re-verified against the pinned digest before it is trusted. A
cached file that no longer matches - because the digest table moved on, or the
file was tampered with - is replaced rather than trusted.

## Metrics

Each run emits exactly one `install-makeutil.result` line, to the log and to
the job summary, over a bounded vocabulary:

| Metric                                         | Meaning                                              |
| ---------------------------------------------- | ---------------------------------------------------- |
| `install-makeutil.result=invalid-input`        | An input was refused before anything ran             |
| `install-makeutil.result=unsupported-platform` | No prebuilt binary for this runner                   |
| `install-makeutil.result=unknown-version`      | The digest table has no entry for this version       |
| `install-makeutil.result=cached`               | `bin-dir` already held the verified binary           |
| `install-makeutil.result=installed`            | Downloaded, verified, and installed                  |
| `install-makeutil.result=digest-mismatch`      | The download did not match the pinned digest         |
| `install-makeutil.result=sidecar-mismatch`     | The download did not match the sidecar               |
| `install-makeutil.result=download-failed`      | The binary or the sidecar could not be fetched       |
| `install-makeutil.result=install-failed`       | The verified binary could not be staged or installed |

Beside it, one `install-makeutil.cache` line reports how the cache was used:

| Metric                         | Meaning                                               |
| ------------------------------ | ----------------------------------------------------- |
| `install-makeutil.cache=hit`   | A restored binary verified and was reused             |
| `install-makeutil.cache=miss`  | Nothing was restored                                  |
| `install-makeutil.cache=stale` | A restored binary was rejected and replaced or failed |

## Inputs

| Name                       | Type   | Description                                                    | Required | Default        |
| -------------------------- | ------ | -------------------------------------------------------------- | -------- | -------------- |
| `version`                  | string | Exact makeutil version to install                              | no       | `0.1.0`        |
| `bin-dir`                  | string | Directory receiving the executable, added to `PATH`            | no       | `~/.local/bin` |
| `expected-sha256-override` | string | Test-only: replaces the pinned digest table entry for this run | no       | `""`           |

`expected-sha256-override` exists so a test workflow can tamper with the
expected digest and assert that a genuinely correct download is still refused.
It has no legitimate use outside such a test; leave it unset in any real
installation.

## Outputs

| Name      | Description                               |
| --------- | ----------------------------------------- |
| `path`    | Absolute path of the installed executable |
| `version` | Version installed, as named by `version`  |
| `result`  | `installed` or `cached`, for this run     |

## Usage

```yaml
- name: Check out the repository
  uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1

- name: Install makeutil
  uses: ./.github/actions/install-makeutil
  with:
    version: 0.1.0
    bin-dir: ${{ runner.temp }}/makeutil-bin

- name: Parse a Makefile
  run: makeutil parse Makefile
```

The repository must be checked out before invoking this local action; use the
relative path without a version suffix.

## Release history

See the [changelog](CHANGELOG.md).
