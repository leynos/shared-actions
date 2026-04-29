# rust-build-release

Build Rust application release artefacts using the repository's `setup-rust` action, `uv`, and `cross`.

FreeBSD targets (for example `x86_64-unknown-freebsd`) require `cross` with a
container runtime when built on non-FreeBSD hosts. The action enforces this so
that builds fail fast if Docker or Podman are unavailable. When a container
runtime is detected, the action exports `CROSS_CONTAINER_ENGINE` for the
duration of the build so that `cross` automatically uses the available engine.

> [!NOTE]
> This action builds release binaries only. Package creation should be handled by
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

| Name          | Type   | Default                    | Description                                                        | Required |
| ------------- | ------ | -------------------------- | ------------------------------------------------------------------ | -------- |
| target        | string | `x86_64-unknown-linux-gnu` | Target triple to build                                             | no       |
| toolchain     | string | (empty)                    | Explicit Rust toolchain override; otherwise the toolchain is resolved from the target repository before falling back to the action default | no |
| project-dir   | string | `.`                        | Path to the Rust project to build                                  | no       |
| manifest-path | string | `Cargo.toml`               | Path to the Cargo manifest (relative to `project-dir` or absolute) | no       |
| bin-name      | string | `rust-toy-app`             | Binary name produced by the build                                  | no       |
| features      | string | (empty)                    | Comma-separated list of Cargo features                             | no       |

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
- uses: ./.github/actions/linux-packages
  with:
    project-dir: rust-toy-app
    bin-name: rust-toy-app
    package-name: rust-toy-app
    target: x86_64-unknown-linux-gnu
    version: 1.2.3
    man-paths: target/generated-man/x86_64-unknown-linux-gnu/release/rust-toy-app.1
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
