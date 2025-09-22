# Linux Packages GitHub Action

Package Rust release artefacts into Linux distribution formats using nFPM.

The action installs `uv` (for inline Python dependencies) and the `nfpm`
command-line interface before invoking the reusable packaging helper shipped in
this repository. It supports Debian, RPM, Alpine, Arch, IPK and SRPM outputs via
nFPM.

## Inputs

| Name | Type | Default | Description | Required |
| ---- | ---- | ------- | ----------- | -------- |
| project-dir | string | `.` | Directory containing the compiled binary, man pages and optional license file. | no |
| package-name | string | _empty_ | Package identifier written to the nFPM manifest. Defaults to `bin-name` when omitted. | no |
| bin-name | string | — | Name of the release binary to package. | yes |
| target | string | `x86_64-unknown-linux-gnu` | Rust target triple used for the build. | no |
| version | string | — | Version number recorded in the package metadata (for example `1.2.3`). | yes |
| formats | string | `deb` | Comma-, space-, or newline-separated list of package formats (for example `deb,rpm` or a multi-line value). | no |
| release | string | _empty_ | Package release or revision override. Uses the packaging helper default when omitted. | no |
| arch | string | _empty_ | Override the nFPM/GOARCH architecture. Auto-detected from `target` when not set. | no |
| maintainer | string | _empty_ | Maintainer entry for the generated package metadata. | no |
| homepage | string | _empty_ | Homepage URL recorded in package metadata. | no |
| license | string | _empty_ | Software license declared in the package metadata. | no |
| section | string | _empty_ | Package section/category used by Debian-based distributions. | no |
| description | string | _empty_ | Long description stored in the package metadata. | no |
| man-paths | string | _empty_ | Whitespace- or newline-separated list of man page paths relative to `project-dir`. | no |
| man-section | string | _empty_ | Default man section applied when a path lacks a suffix (for example `1`). | no |
| man-stage | string | _empty_ | Directory used to stage gzipped man pages before invoking nFPM. | no |
| binary-dir | string | _empty_ | Cargo `target` directory containing build artefacts. | no |
| outdir | string | _empty_ | Directory where packages will be written. | no |
| config-path | string | _empty_ | Location to write the generated `nfpm.yaml` configuration. | no |
| deb-depends | string | _empty_ | Whitespace- or newline-separated Debian runtime dependencies (each entry becomes a separate dependency in the generated manifest). | no |
| rpm-depends | string | _empty_ | Whitespace- or newline-separated RPM runtime dependencies. Falls back to Debian deps when omitted. | no |

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

The action assumes a release binary is already present at
`<project-dir>/target/<target>/release/<bin-name>` (with `project-dir` defaulting
to `.` this resolves to `target/<target>/release/<bin-name>`), and that any man
pages referenced via `man-paths` exist relative to `project-dir`. List inputs
such as `formats`, `man-paths`, `deb-depends` and `rpm-depends` accept comma-,
space- or newline-separated values; when provided as a multi-line YAML string
each line is treated as a distinct entry.

## Release History

See [CHANGELOG](CHANGELOG.md).
