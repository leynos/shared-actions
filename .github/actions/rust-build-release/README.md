# rust-build-release

Build Rust application release artefacts using the repository's `setup-rust`
action, `uv`, and `cross`.

FreeBSD targets (for example `x86_64-unknown-freebsd`) require `cross` with a
container runtime when built on non-FreeBSD hosts. The action enforces this so
that builds fail fast if Docker or Podman are unavailable. When a container
runtime is detected, the action exports `CROSS_CONTAINER_ENGINE` for the
duration of the build so that `cross` automatically uses the available engine.

> [!NOTE]
> This action builds release binaries only. Package creation should be handled
> by
> the platform-specific composite actions:
>
> - Linux: [`linux-packages`](../linux-packages)
> - macOS: [`macos-package`](../macos-package)
> - Windows: [`windows-package`](../windows-package)
>
> When run on Linux runners the action also supports cross-compiling
> `x86_64-unknown-illumos` targets. The staged artefacts are emitted beneath an
> `illumos/amd64` directory alongside the Linux distributions.

The `uv` Python package manager is installed automatically to execute the build
script.

Toolchains are resolved from the target repository in this order: explicit
`toolchain` input, repository `rust-toolchain.toml` or `rust-toolchain`,
manifest `rust-version`, then the action's bundled fallback version.

## Inputs

| Name                    | Type    | Default                    | Description                       | Required |
| ----------------------- | ------- | -------------------------- | --------------------------------- | -------- |
| target                  | string  | `x86_64-unknown-linux-gnu` | Target triple to build            | no       |
| toolchain               | string  | (empty)                    | Explicit Rust toolchain override  | no       |
| project-dir             | string  | `.`                        | Path to the Rust project to build | no       |
| manifest-path           | string  | `Cargo.toml`               | Cargo manifest path               | no       |
| bin-name                | string  | `rust-toy-app`             | Binary name produced by the build | no       |
| features                | string  | (empty)                    | Comma-separated Cargo features    | no       |
| skip-man-page-discovery | boolean | `false`                    | Post-build man opt-out            | no       |
| rustflags               | string  | (empty)                    | RUSTFLAGS exported pre-setup      | no       |
| cache-provider          | string  | `github`                   | Nested setup-rust cache owner     | no       |
| use-sccache             | boolean | `true`                     | Nested setup-rust sccache switch  | no       |

When `toolchain` is empty, the action resolves the toolchain from the target
repository before falling back to the action default. `manifest-path` may be
relative to `project-dir` or absolute.

`rustflags` defaults to empty, which leaves the environment untouched. Left
empty, the nested `setup-rust` step exports its own `-D warnings` default
whenever `RUSTFLAGS` is unset, and an ambient `RUSTFLAGS` shadows the
project's `build.rustflags` in `.cargo/config.toml` — this input exists to
solve that problem. Setting a value exports it before toolchain setup so the
build honours it (for example, a required `-Z` flag such as
`-Zpolonius=next`). A pre-existing `RUSTFLAGS` in the environment always
wins, including when it is deliberately set to the empty string.

### Caller-owned caches

`cache-provider` and `use-sccache` are forwarded verbatim to the nested
`setup-rust` step; this action adds no caches of its own. The default
`cache-provider: github` keeps that step's Cargo and uv GitHub archive caches,
and `use-sccache: 'true'` keeps its GitHub-backed sccache integration.

Pass `cache-provider: external` whenever the caller already owns those paths,
for example on Ubicloud or with a Namespace cache volume mounted before the
build. External mode disables the nested archive caches; it does not mount a
replacement. Pair it with `use-sccache: 'false'` when the caller installs its
own sccache binary, sets `RUSTC_WRAPPER=sccache`, and mounts that cache
directory, so only one owner writes each path. The two inputs are independent:
the compiler cache and the Cargo and uv archive caches can be switched
separately. An unrecognized `cache-provider` fails before toolchain setup runs.

`use-sccache: 'true'` inherits the nested step's whole sccache contract, which
now includes starting the server from a `run:` step and restoring a caller's
`ACTIONS_CACHE_SERVICE_V2` after `mozilla-actions/sccache-action` overwrites it.
That makes `true` the right setting on Ubicloud, provided
`export-ubicloud-cache-credentials` runs before this action. On a GitHub-hosted
runner prefer the local-disk arm instead: pass `use-sccache: 'false'`, install a
pinned sccache, and cache `SCCACHE_DIR` yourself, because the GitHub Actions
backend spends most of what it saves there. See the `setup-rust`
[README](../setup-rust/README.md) for the measurements.

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
- uses: ./.github/actions/rust-build-release
  with:
    target: x86_64-unknown-linux-gnu
    project-dir: rust-toy-app
    cache-provider: external
    use-sccache: 'false'
```

By default, Linux and illumos staging discovers man pages generated during
`cargo build` at `target/generated-man/<target>/release/<bin>.1`, then falls
back to Cargo `OUT_DIR` output from `build.rs`. Set
`skip-man-page-discovery: 'true'` only when a later workflow step generates and
validates the man page.

## Outputs

None.

## Usage

```yaml
# Local usage (same repository)
- uses: ./.github/actions/rust-build-release
  with:
    target: x86_64-unknown-linux-gnu
    project-dir: rust-toy-app
    manifest-path: Cargo.toml
    bin-name: rust-toy-app

# Remote usage (after tagging this repo with v1)
- uses: leynos/shared-actions/.github/actions/rust-build-release@v1
  with:
    target: x86_64-unknown-linux-gnu
    project-dir: rust-toy-app
    manifest-path: Cargo.toml
    bin-name: rust-toy-app

# Build with specific Cargo features enabled
- uses: ./.github/actions/rust-build-release
  with:
    target: x86_64-unknown-linux-gnu
    toolchain: nightly-2026-03-26
    project-dir: rust-toy-app
    bin-name: rust-toy-app
    features: "verbose,experimental"
```

```yaml
# Package artefacts after building
- id: find-linux-manpage
  shell: bash
  working-directory: rust-toy-app
  run: |
    set -euo pipefail
    manpage="target/generated-man/${TARGET}/release/rust-toy-app.1"
    test -f "$manpage"
    echo "path=${manpage}" >> "$GITHUB_OUTPUT"
  env:
    TARGET: x86_64-unknown-linux-gnu
- uses: ./.github/actions/linux-packages
  with:
    project-dir: rust-toy-app
    bin-name: rust-toy-app
    package-name: rust-toy-app
    target: x86_64-unknown-linux-gnu
    version: 1.2.3
    man-paths: ${{ steps.find-linux-manpage.outputs.path }}
```

### Cross-compiling illumos artefacts

The action can build illumos binaries from a Linux runner using `cross`:

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/rust-build-release
        with:
          target: x86_64-unknown-illumos
          project-dir: rust-toy-app
```

The Stage artefacts step maps the resulting files into
`dist/rust-toy-app_illumos_amd64/` so they can be uploaded or packaged by
downstream workflows.

## Release History

See [CHANGELOG](CHANGELOG.md).
