# Install mdtablefix

Install a pinned `mdtablefix` from its prebuilt release, never from source.

The action probes for the pinned version first, obtains `cargo-binstall` only
when the runner has none, then runs a hardened `cargo binstall` that cannot
fall back to a compile. A runner with no prebuilt asset fails closed.

## Platforms

This action requires `mdtablefix` 0.5.1 or later and refuses anything earlier.
0.5.0 published Linux archives only and declared `binstall` metadata that
cargo-binstall rejects, so supporting both would mean carrying two platform
lists and a conditional metadata override for a version nothing pins.

0.5.1 publishes archives for Linux and macOS on `x86_64` and `aarch64`, and for
Windows on `x86_64`
([leynos/mdtablefix#459](https://github.com/leynos/mdtablefix/issues/459)).

| Runner              | Outcome                             |
| ------------------- | ----------------------------------- |
| `Linux` / `X64`     | Installed from the prebuilt archive |
| `Linux` / `ARM64`   | Installed from the prebuilt archive |
| `macOS` / `X64`     | Installed from the prebuilt archive |
| `macOS` / `ARM64`   | Installed from the prebuilt archive |
| `Windows` / `X64`   | Installed from the prebuilt archive |
| `Windows` / `ARM64` | Fails closed, `result=no-prebuilt`  |
| anything else       | Fails closed, `result=no-prebuilt`  |

The action never compiles `mdtablefix`. Windows on `aarch64` fails closed
because 0.5.1 publishes no aarch64 Windows archive, not because the action has
not been taught the platform.

A FreeBSD archive is published as well and has no entry, because GitHub
publishes no FreeBSD runner label for `runner.os` to report. Add one when a
label exists; until then the entry would be untestable.

A Windows caller may pass `${{ runner.temp }}/...` directly. That arrives as a
native path such as `D:\a\_temp\bin`, which Git Bash does not treat as
absolute, so the action converts it with `cygpath` rather than making every
caller wrap it.

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
    key: mdtablefix-0.5.1-${{ runner.os }}-${{ runner.arch }}

- name: Install mdtablefix
  uses: ./.github/actions/install-mdtablefix
  with:
    version: 0.5.1
    bin-dir: ${{ runner.temp }}/mdtablefix-bin
```

## No `bin-dir` override

`cargo binstall` is invoked without `--bin-dir`, so the crate's own `binstall`
metadata decides where the executable lives in the archive.

That was not always so. 0.5.0 declared `bin-dir = "."`, which cargo-binstall
rejects with `bin-dir configuration provided generates empty source path`, and
with the compile strategy correctly disabled the install then failed outright
([leynos/mdtablefix#458](https://github.com/leynos/mdtablefix/issues/458)), so
this action passed an override naming the executable itself. 0.5.1 carries
correct metadata and the version floor refuses anything earlier, so an override
would now second-guess a manifest the crate is responsible for. A test asserts
the flag has not returned.

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
    version: 0.5.1

- name: Check formatting
  run: make check-fmt
```

The repository must be checked out before invoking this local action; use the
relative path without a version suffix. The runner must provide Bash 3.2 or
later; the fragments avoid every Bash 4 construct so macOS runners behave the
same way.

Failures are reported by an explicit exit-status check rather than an `ERR`
trap, which is what the sibling installers use. Both work on a runner; a
checked status is additionally invariant to how the fragment is invoked.

## Release history

See the [changelog](CHANGELOG.md).
