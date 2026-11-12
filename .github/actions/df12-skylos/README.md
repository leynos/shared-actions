# df12 Skylos dead-code gate

Provision the pinned `df12-python-lints` package with its `skylos` extra into
an isolated `uv` tool environment, then run `df12-skylos check` over a
repository. The package owns the gate's behaviour, the one Skylos pin and the
configuration rules; this action only provisions it and reports the result. A
consumer keeps `[tool.skylos]`, `[tool.df12_skylos]` and their reasoned
exceptions in its own `pyproject.toml`.

## Inputs

| Name               | Type   | Description                                                   | Required | Default                                    |
| ------------------ | ------ | ------------------------------------------------------------- | -------- | ------------------------------------------ |
| `python-lints-sha` | string | Full 40-digit lowercase commit of `leynos/df12-python-lints`. | No       | `1d6c3bba594651823c9ba8e8b40265cae59a1c52` |
| `repository`       | string | Directory holding the `pyproject.toml` to check.              | No       | `.`                                        |
| `python-version`   | string | Interpreter for the tool environment, such as `3.14`.         | No       | `3.14`                                     |

The default commit is the v0.4.0 release. A branch, tag, short SHA or
upper-case digest is refused before `uv` runs.

## Outputs

| Name          | Description                                                                             |
| ------------- | --------------------------------------------------------------------------------------- |
| `exit-status` | Status of `df12-skylos check`: `0` clean, `1` blocking findings, `2` invalid or failed. |

The step fails on any non-zero status. Use `continue-on-error` and read
`exit-status` to assert a failing outcome.

## Results

The step prints and records in the step summary one
`metric df12-skylos.result=<value>` line. The values are `clean`, `blocking`,
`invalid`, `invalid-input` (a malformed input) and `missing-prerequisite` (no
`uv` on `PATH`).

## Behaviour

- `setup-uv` runs with its cache disabled and the shared prune step runs before
  the first `uv` invocation, so a cache restored by the calling workflow cannot
  hand over an interpreter-bound environment.
- The package is installed from
  `git+https://github.com/leynos/df12-python-lints.git@<sha>` with the `skylos`
  extra, which carries the single Skylos pin; `df12-skylos` fails closed if the
  installed backend differs.
- Linux and macOS runners only.

## Usage

```yaml
- name: Dead-code gate
  uses: leynos/shared-actions/.github/actions/df12-skylos@<40-hex-commit-sha>
```
