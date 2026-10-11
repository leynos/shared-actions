# Install YAML lint tools

Install the pinned tools that lint YAML and GitHub Actions workflows:
`yamllint`, `actionlint` and `shellcheck`.

`actionlint` runs `shellcheck` over every `run:` script it finds on `PATH`, so
installing the three together keeps local and Continuous Integration (CI)
results identical. `yamllint` is installed through `uv` with an exact `==` pin.
`actionlint` and `shellcheck` come only from their official release archives
and are installed only after the archive's Secure Hash Algorithm 256-bit
(SHA-256) digest matches the digest embedded in the action. A mismatch, an
unsupported platform or a missing prerequisite fails the step.

## Inputs

| Name   | Type | Description                            | Required | Default |
| ------ | ---- | -------------------------------------- | -------- | ------- |
| _None_ | -    | Versions are pinned inside the action. | -        | -       |

## Outputs

| Name   | Description                  |
| ------ | ---------------------------- |
| _None_ | The action emits no outputs. |

## Pins

| Tool         | Version  | Source                                 |
| ------------ | -------- | -------------------------------------- |
| `yamllint`   | `1.38.0` | PyPI through `uv tool install`         |
| `actionlint` | `1.7.12` | `rhysd/actionlint` release archives    |
| `shellcheck` | `0.11.0` | `koalaman/shellcheck` release archives |

Supported runners are Linux and macOS on x86-64 and ARM64. Windows runners are
not supported: the action fails there with
`no verified release is configured for Windows/X64` before it downloads
anything. The runner must provide `uv`, `curl`, `tar` and `install`, and
`sha256sum` or `shasum`.

## Usage

```yaml
- name: Install YAML lint tools
  uses: leynos/shared-actions/.github/actions/install-yaml-lint@<SHA>

- name: Lint
  run: make lint
```

The step must precede the one that runs `make lint`, which then finds
`yamllint` and `actionlint` on `PATH`.

## Release history

See [CHANGELOG](./CHANGELOG.md).
