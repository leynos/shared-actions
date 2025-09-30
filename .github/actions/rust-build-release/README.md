# rust-build-release

Build Rust application release artefacts using the repository's `setup-rust` action, `uv`, and `cross`.

FreeBSD targets (for example `x86_64-unknown-freebsd`) require `cross` with a
container runtime when built on non-FreeBSD hosts. The action enforces this so
that builds fail fast if Docker or Podman are unavailable.

> [!NOTE]
> This action builds release binaries only. Package creation should be handled by
> the platform-specific composite actions:
>
> - Linux: [`linux-packages`](../linux-packages)
> - macOS: [`macos-package`](../macos-package)
> - Windows: [`windows-package`](../windows-package)

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

## Release History

See [CHANGELOG](CHANGELOG.md).
