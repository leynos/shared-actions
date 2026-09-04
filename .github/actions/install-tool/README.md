# install-tool

Install a pinned, digest-verified tool from this repository's tool manifest.

## Usage

```yaml
- uses: leynos/shared-actions/.github/actions/install-tool@v1
  with:
    tool: cargo-nextest
    version: 0.9.143
```

## Inputs

| Name | Type | Default | Description | Required |
| --- | --- | --- | --- | --- |
| tool | string | | Manifest tool name, for example `sccache` | yes |
| version | string | | Exact manifest version; there is no `latest` | yes |
| bin-dir | string | `~/.cargo/bin` | Directory installed into and added to `PATH` | no |

## Outputs

| Name | Description |
| --- | --- |
| path | Absolute path of the installed binary |
| version | Version installed, as named in the manifest |
| cache-hit | `true` when the requested version was already installed |

## What it installs, and what it will not

Only what [`.github/tool-manifest.toml`](../../tool-manifest.toml) lists, at a
version the caller names. There is no floating version and no lookup of a
latest release, because that is a network call in the critical path and a
dependency that changes underneath you: an unpinned lookup timed out on
shared-actions #440 and failed a job with
`Unable to locate executable file: undefined`, a red check with no step log
behind it.

A version that is not in the manifest fails closed, and says so, rather than
reaching for whatever is newest. Adding the version to the manifest is the fix;
making it float is not.

A target the tool publishes no archive for also fails closed, naming the
targets it does offer. Dylint is the live example: version 6.0.4 ships Linux
archives only, so a macOS or Windows lane gets a refusal here rather than a
confusing failure several steps later. Asked for upstream in
[trailofbits/dylint#2068](https://github.com/trailofbits/dylint/issues/2068).

## How a tool is verified

Three checks, in this order, each catching something the others cannot.

The **pinned digest** is the trust anchor. It was computed from an independent
download of the archive when the entry was written, not copied from an upstream
sidecar. Where a sidecar exists it was fetched and compared, and the entry
records the result in `sidecar-verified`: `true` when it agreed, `absent` when
upstream publishes none, `false` when one exists and could not be read. The
sidecar corroborates the pin's provenance and is never its source.

The archive is hashed by feeding it to `sha256sum` on **stdin**,
never by file name, because GNU `sha256sum` escapes its output line when the
name contains a backslash and a Windows path therefore produced a leading
backslash that rejected a correct archive.

The **member path** from the manifest says where the binary sits inside the
archive. Upstreams disagree: cargo-nextest and cargo-llvm-cov put it at the
root, while cargo-audit, dylint and sccache put it under one directory.
`--strip-components=1` is right for one shape and destroys the other.

The **installed binary** is asked its own version, on the cached path as well
as the fresh one. A digest proves the bytes arrived intact; only running it
proves the right thing is where the caller will look. One tool cannot be asked:
`dylint-link` refuses every argument unless `RUSTUP_TOOLCHAIN` is set, so its
entry records that and the action reports `install-tool.verify=unsupported`
rather than passing a check it never made.

The archive extractor is chosen by the asset's **extension**, never by probing
what `tar` resolves to. Git Bash puts MSYS GNU tar ahead of the system bsdtar
on Windows, and GNU tar cannot read a zip; choosing by capability is what broke
`install-whitaker` in #446.

## Cache ownership

This action installs into `bin-dir` and **archives nothing**. A lane that wants
an installed tool to survive between jobs owns that cache step and its key, the
same division as `setup-rust` and its compiler output, and for the same reason:
one key with one writer, restored on pull requests and saved by one job on a
push to `main`.

The action does probe for what is already there. A second call in the same job,
or a call after a cache the caller restored, finds the exact version and skips
the download; that is what `cache-hit` reports. A binary of the right name and
the wrong version counts as a miss, not a hit.

## Metrics

Each run emits bounded `metric` lines, to the log for a scraper and to the job
summary for a reader:

| Metric | Values |
| --- | --- |
| `install-tool.resolve` | `ok`, `no-python`, `manifest-unreadable`, `unknown-tool`, `unknown-version`, `unsupported-runner`, `unsupported-target`, `unsupported-extension` |
| `install-tool.sidecar-verified` | `true`, `false`, `absent` |
| `install-tool.cache` | `hit`, `hit-unverified`, `miss`, `stale` |
| `install-tool.download` | `ok`, `failed` |
| `install-tool.digest` | `verified`, `mismatch` |
| `install-tool.install` | `ok`, `failed`, `missing-member`, `unsupported-extension` |
| `install-tool.verify` | `ok`, `mismatch`, `missing`, `unsupported` |
| `install-tool.result` | `installed`, `cached` |

Keep the names fixed and the values inside those sets. A value that varies with
the run gives the series unbounded cardinality and makes it useless to
aggregate.

## Adding or bumping a tool

Edit [`.github/tool-manifest.toml`](../../tool-manifest.toml). Download every
archive, compute its SHA-256, compare against the upstream sidecar where one
exists, and record the result in `sidecar`. Never copy a digest out of a
sidecar without downloading the archive, because that records only that the
sidecar agrees with itself.
