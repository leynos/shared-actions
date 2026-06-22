# Linux Packages GitHub Action

Package Rust release artefacts into Linux distribution formats using nFPM.

The action installs `uv` (for inline Python dependencies) and the `nfpm`
command-line interface before invoking the reusable packaging helper shipped in
this repository. It supports Debian, RPM, Alpine, Arch, IPK and SRPM outputs via
nFPM.

## Inputs

| Name | Type | Default | Description | Required |
| ---- | ---- | ------- | ----------- | -------- |
| project-dir | string | `.` | Binary, man, license dir | no |
| package-name | string | _empty_ | Package ID (defaults to bin) | no |
| bin-name | string | — | Binary name | yes |
| target | string | `x86_64-unknown-linux-gnu` | Rust target triple | no |
| version | string | — | Package version | yes |
| formats | string | `deb` | Formats (e.g. `deb,rpm`) | no |
| release | string | _empty_ | Release/revision override | no |
| arch | string | _empty_ | Architecture (auto-detected) | no |
| maintainer | string | _empty_ | Package maintainer | no |
| homepage | string | _empty_ | Homepage URL | no |
| license | string | _empty_ | Software license | no |
| section | string | _empty_ | Package section | no |
| description | string | _empty_ | Package description | no |
| man-paths | string | _empty_ | Man page paths | no |
| man-section | string | _empty_ | Default man section | no |
| man-stage | string | _empty_ | Man staging directory | no |
| binary-dir | string | _empty_ | Cargo target directory | no |
| outdir | string | _empty_ | Output directory | no |
| config-path | string | _empty_ | nfpm config path | no |
| deb-depends | string | _empty_ | Debian dependencies | no |
| rpm-depends | string | _empty_ | RPM dependencies | no |

Before invoking sibling actions the composite mirrors the repository snapshot
that GitHub already downloaded for the action into a local `_self/` directory.
This guarantees that nested `./_self/.github/actions/*` references resolve to the
same commit without performing an additional network checkout. For private
repositories GitHub performs the initial download using the workflow’s
configured credentials; once the runner has the action payload this mirroring
step works without additional tokens. Local workflows that reference the action
via a relative path reuse the same mirroring logic, copying the repository
contents from the workspace instead of the runner cache.

## Outputs

None.

## Usage

```yaml
# Local usage (same repository)
- uses: ./.github/actions/linux-packages
  with:
    project-dir: rust-toy-app
    bin-name: rust-toy-app
    package-name: rust-toy-app
    target: x86_64-unknown-linux-gnu
    version: 1.2.3
    formats: deb,rpm
    man-paths: |
      dist/rust-toy-app_linux_amd64/rust-toy-app.1

# Remote usage (after tagging this repo with v1)
- uses: leynos/shared-actions/.github/actions/linux-packages@v1
  with:
    project-dir: rust-toy-app
    bin-name: rust-toy-app
    package-name: rust-toy-app
    target: x86_64-unknown-linux-gnu
    version: 1.2.3
    formats: deb
```

The action assumes a release binary is already present at:

`<project-dir>/target/<target>/release/<bin-name>`

With the default `project-dir` of `.`, this resolves to
`./target/<target>/release/<bin-name>`. Any man pages referenced via `man-paths`
must exist relative to `project-dir`. List inputs such as `formats`, `man-paths`,
`deb-depends` and `rpm-depends` accept comma-, space- or newline-separated
values; when provided as a multi-line YAML string each line is treated as a
distinct entry.

## Release History

See [CHANGELOG](CHANGELOG.md).
