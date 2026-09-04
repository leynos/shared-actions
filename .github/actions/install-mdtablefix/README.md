# Install mdtablefix

Install a pinned `mdtablefix` from its prebuilt release, never from source.

The action probes for the pinned version first, obtains `cargo-binstall` only
when the runner has none, then runs a hardened `cargo binstall` that cannot
fall back to a compile. A runner with no prebuilt asset fails closed.

## Platforms

`mdtablefix` 0.5.0 publishes prebuilt archives for Linux gnu on `x86_64` and
`aarch64` only, and its `binstall` metadata override is gated on the same
target configuration. macOS and Windows have no asset at all.

| Runner            | Outcome                             |
| ----------------- | ----------------------------------- |
| `Linux` / `X64`   | Installed from the prebuilt archive |
| `Linux` / `ARM64` | Installed from the prebuilt archive |
| `macOS` / any     | Fails closed, `result=no-prebuilt`  |
| `Windows` / any   | Fails closed, `result=no-prebuilt`  |

The action never compiles `mdtablefix`. A consumer with a Windows or macOS
formatter lane keeps its own documented exception until `mdtablefix` publishes
assets for that platform; widen the platform gate here, and the runner-backed
workflow with it, when it does.

The platform is rejected before the cache is consulted, so a cached executable
cannot report success on a runner this action could not have installed.

## Caching

The action caches nothing itself, so one cache key keeps one owner. The caller
owns `bin-dir` and restores it, and the action's first step reports
`install-mdtablefix.result=cached` and exits when the executable there already
reports the pinned version.

```yaml
- name: Restore mdtablefix
  uses: actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0
  with:
    path: ${{ runner.temp }}/mdtablefix-bin
    key: mdtablefix-0.5.0-${{ runner.os }}-${{ runner.arch }}

- name: Install mdtablefix
  uses: ./.github/actions/install-mdtablefix
  with:
    version: 0.5.0
    bin-dir: ${{ runner.temp }}/mdtablefix-bin
```

## The `bin-dir` override

`cargo binstall` is invoked with `--bin-dir '{ bin }{ binary-ext }'`. The
`mdtablefix` 0.5.0 crate declares `bin-dir = "."` in its `binstall` metadata,
which cargo-binstall 1.22 rejects with
`bin-dir configuration provided generates empty source path`; with the compile
strategy correctly disabled the install then fails outright. The published
archive holds the executable at its root, so the override names it correctly.
Remove the override, and the test that pins it, once a pinned release carries
fixed metadata
([leynos/mdtablefix#458](https://github.com/leynos/mdtablefix/issues/458)).

## Obtaining cargo-binstall

`cargo-binstall` is probed by running `cargo binstall -V`, not by looking for a
name on `PATH`. A Cargo subcommand shim can exist and still be unusable, which
is the presence-probe defect recorded in issue #420. When the probe fails, the
upstream `cargo-bins/cargo-binstall` action installs the requested version,
pinned by commit SHA. A composite action cannot make a `uses:` step conditional
on a shell result directly, so the probe publishes a step output and the
`uses:` step carries an `if:` over it.

That upstream step is the one step whose failure this action cannot annotate
from inside, so a `failure()`-guarded step immediately after it reports
`install-mdtablefix.result=binstall-unavailable`. Without it a bad
`binstall-version`, or a failed download, would stop the action with no result
metric at all.

## Metrics

Each run emits exactly one `install-mdtablefix.result` line to the job summary,
over a bounded vocabulary, and at most one `install-mdtablefix.binstall` line.

| Metric                                           | Meaning                                      |
| ------------------------------------------------ | -------------------------------------------- |
| `install-mdtablefix.result=invalid-input`        | An input was refused before anything ran     |
| `install-mdtablefix.result=cached`               | `bin-dir` already held the pinned version    |
| `install-mdtablefix.result=installed`            | Installed and verified                       |
| `install-mdtablefix.result=no-prebuilt`          | No prebuilt release for this runner          |
| `install-mdtablefix.result=install-failed`       | `cargo binstall` failed                      |
| `install-mdtablefix.result=binstall-unavailable` | cargo-binstall could not be installed        |
| `install-mdtablefix.result=version-mismatch`     | The installed version was not the pinned one |
| `install-mdtablefix.binstall=present`            | The runner already had a usable binstall     |
| `install-mdtablefix.binstall=installed`          | The pinned upstream action provided it       |

## Inputs

| Name               | Type   | Description                                         | Required | Default        |
| ------------------ | ------ | --------------------------------------------------- | -------- | -------------- |
| `version`          | string | Exact `mdtablefix` version to install               | yes      | none           |
| `binstall-version` | string | `cargo-binstall` version to install when absent     | no       | `1.22.0`       |
| `bin-dir`          | string | Directory receiving the executable, added to `PATH` | no       | `~/.local/bin` |

## Outputs

| Name | Description                     |
| ---- | ------------------------------- |
| None | This action exposes no outputs. |

The executable's directory is appended to `GITHUB_PATH`, so later steps in the
job can call `mdtablefix` by name.

## Usage

```yaml
- name: Check out the repository
  uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1

- name: Install mdtablefix
  uses: ./.github/actions/install-mdtablefix
  with:
    version: 0.5.0

- name: Check formatting
  run: make check-fmt
```

The repository must be checked out before invoking this local action; use the
relative path without a version suffix. The runner must provide Bash 3.2 or
later; the fragments avoid every Bash 4 construct so macOS runners behave the
same way.

Failures are reported by an explicit exit-status check rather than an `ERR`
trap. Bash 3.2 did not run the trap when cargo-binstall failed on a macOS
runner, which left the step failing silently, with no annotation and no metric.

## Release history

See the [changelog](CHANGELOG.md).
