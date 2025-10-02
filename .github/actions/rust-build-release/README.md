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

## Inputs

| Name        | Type   | Default                    | Description                           | Required |
| ----------- | ------ | -------------------------- | ------------------------------------- | -------- |
| target      | string | `x86_64-unknown-linux-gnu` | Target triple to build                | no       |
| project-dir | string | `.`                        | Path to the Rust project to build     | no       |
| bin-name    | string | `rust-toy-app`             | Binary name produced by the build     | no       |

## Outputs

None.

## Usage

```yaml
# Local usage (same repository)
- uses: ./.github/actions/rust-build-release
  with:
    target: x86_64-unknown-linux-gnu
    project-dir: rust-toy-app
    bin-name: rust-toy-app

# Remote usage (after tagging this repo with v1)
- uses: leynos/shared-actions/.github/actions/rust-build-release@v1
  with:
    target: x86_64-unknown-linux-gnu
    project-dir: rust-toy-app
    bin-name: rust-toy-app
```

```yaml
# Package artefacts after building
- id: find-linux-manpage
  shell: bash
  working-directory: rust-toy-app
  run: |
    set -euo pipefail
    manpage=$(find target/${TARGET}/release/build -name 'rust-toy-app.1' -print -quit)
    test -n "$manpage"
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

The Stage artifacts step maps the resulting files into
`dist/rust-toy-app_illumos_amd64/` so they can be uploaded or packaged by
downstream workflows.

## Release History

See [CHANGELOG](CHANGELOG.md).
