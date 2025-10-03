# Validate Linux Packages GitHub Action

Validate Debian and RPM packages produced by the `linux-packages` action (or
any nfpm-based build) by inspecting metadata and installing them inside an
isolated root filesystem using the bundled `polythene` sandbox. The composite
action ensures the required sandbox tooling (`bubblewrap` and `proot`) is
present before validation begins.

## Inputs

| Name | Type | Default | Description | Required |
| ---- | ---- | ------- | ----------- | -------- |
| project-dir | string | `.` | Directory containing the generated packages. | no |
| package-name | string | _empty_ | Package identifier recorded in the package metadata. | no |
| bin-name | string | — | Installed binary name to validate. | yes |
| target | string | `x86_64-unknown-linux-gnu` | Target triple used when building the artefacts. | no |
| version | string | — | Semantic version recorded in the package metadata. Leading `v` prefixes are ignored. | yes |
| release | string | _empty_ | Package release or revision. | no |
| arch | string | _empty_ | Override the nfpm architecture (auto-detected from `target` when blank). | no |
| formats | string | `deb` | Comma-, space-, or newline-separated list of package formats to validate (`deb`, `rpm`, …). | no |
| packages-dir | string | _empty_ | Directory containing the built packages. | no |
| expected-paths | string | _empty_ | Additional absolute paths that must be present in the package payload (defaults to `/usr/bin/<bin-name>`). | no |
| executable-paths | string | _empty_ | Subset of `expected-paths` that must be executable. Defaults to `/usr/bin/<bin-name>`. | no |
| verify-command | string | _empty_ | Optional command executed inside the sandbox after installation (for example `"/usr/bin/<bin-name> --version"`). | no |
| deb-base-image | string | `docker.io/library/debian:bookworm` | Container image used to verify Debian packages. | no |
| rpm-base-image | string | `docker.io/library/rockylinux:9` | Container image used to verify RPM packages. | no |
| polythene-path | string | _empty_ | Override path to the `polythene.py` helper. Falls back to the copy shipped with the linux-packages action. | no |
| polythene-store | string | _empty_ | Reuse an existing polythene store directory. A temporary directory is used when blank. | no |
| sandbox-timeout | string | _empty_ | Timeout (seconds) applied to sandbox pull and exec operations. | no |

## Outputs

None.

## Usage

```yaml
# Local usage (same repository)
- uses: ./.github/actions/linux-packages
  id: package-linux
  with:
    project-dir: rust-toy-app
    bin-name: rust-toy-app
    target: x86_64-unknown-linux-gnu
    version: 1.2.3

- uses: ./.github/actions/validate-linux-packages
  with:
    project-dir: rust-toy-app
    bin-name: rust-toy-app
    target: x86_64-unknown-linux-gnu
    version: 1.2.3
    formats: deb
    expected-paths: |
      /usr/bin/rust-toy-app
      /usr/share/man/man1/rust-toy-app.1.gz

# Remote usage (after tagging this repo with v1)
- uses: leynos/shared-actions/.github/actions/validate-linux-packages@v1
  with:
    bin-name: rust-toy-app
    version: 1.2.3
    target: x86_64-unknown-linux-gnu
    formats: deb,rpm
    expected-paths: |
      /usr/bin/rust-toy-app
```

Paths supplied to `expected-paths` and `executable-paths` must already be
canonical absolute strings.
Redundant separators or `.`/`..` segments are rejected to prevent ambiguous
validation rules.

The action expects packages to be available in `<project-dir>/dist` unless
`packages-dir` is provided. When Debian packages are validated the sandbox
installs them with `dpkg -i`; RPM packages use `rpm -i --nodeps`. Both flows
verify the package metadata, ensure the expected files exist, and optionally run
a supplied command inside the sandbox.

## Release History

See [CHANGELOG](CHANGELOG.md).
