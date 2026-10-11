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

From this repository, check it out and reference the action by path:

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
  with:
    persist-credentials: false

- name: Install YAML lint tools
  uses: ./.github/actions/install-yaml-lint

- name: Lint
  run: make lint
```

From another repository, reference the action by the full 40-character commit
SHA of the shared-actions revision to use; a tag or branch is not accepted by
the Concordat `yaml-lint-baseline` rule:

```yaml
- name: Install YAML lint tools
  uses: leynos/shared-actions/.github/actions/install-yaml-lint@<40-hex-commit-sha>
```

The step must precede the one that runs `make lint`, which then finds
`yamllint` and `actionlint` on `PATH`. The verified release directory is added
to `PATH` after uv's, so it wins over any other `actionlint` or `shellcheck`.

## Metrics

The action logs, and writes to the step summary, one bounded line per decision:
`install-yaml-lint.actionlint` and `install-yaml-lint.shellcheck` are
`cache-hit` or `install`, and `install-yaml-lint.result` is `ok`,
`missing-prerequisite`, `unsupported-platform`, `download-failed`,
`digest-mismatch`, `extract-failed`, `missing-member`, `uv-install-failed` or
`version-mismatch`. The values are a fixed vocabulary, never a tool's own
output.

## Release history

See [CHANGELOG](./CHANGELOG.md).
