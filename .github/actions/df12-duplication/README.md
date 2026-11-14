# df12 duplication gate

Provision the pinned `df12-python-lints` package with its `duplication` extra
into an isolated `uv` tool environment, install the `nose` detector release
that the repository's `[tool.nose].version` names, and run
`df12-duplication check`. The package owns the gate, the exception editor and
the table of release digests; this action only provisions it and reports the
result. A consumer keeps `[tool.nose]` and its reasoned
`[[tool.duplication_gate.allow]]` entries in its own `pyproject.toml`.

## Inputs

| Name             | Type   | Description                                                | Required | Default |
| ---------------- | ------ | ---------------------------------------------------------- | -------- | ------- |
| `repository`     | string | Directory holding the `pyproject.toml` with `[tool.nose]`. | No       | `.`     |
| `python-version` | string | Interpreter for the tool environment, such as `3.14`.      | No       | `3.14`  |

The package commit is a literal in the action (df12-python-lints main after
v0.4.0, `b83e3eb850de93b3f9ce7fbc9ffdb429925714dc`, which adds the `allow`
coverage check and runs the Skylos scan under a fixed hash seed), so an audit
reads the exact source. Bump it in both df12 actions together; consumers move
by bumping the SHA that references the action.

## Outputs

| Name          | Description                                                                                           |
| ------------- | ----------------------------------------------------------------------------------------------------- |
| `exit-status` | Status of `check`: `0` clean, `1` blocking families, `2` invalid or failed (also a failed `install`). |

The step fails on any non-zero status. Use `continue-on-error` and read
`exit-status` to assert a failing outcome.

## Results

The step prints and records in the step summary one
`metric df12-duplication.result=<value>` line. The values are `clean`,
`blocking`, `invalid`, `install-failed`, `invalid-input` (a malformed input) and
`missing-prerequisite` (no `uv` on `PATH`).

## Behaviour

- `df12-duplication install` downloads the pinned release archive from
  `github.com/corca-ai/nose` and verifies its SHA-256 against the digest table
  shipped in `df12-python-lints`
  (`df12_python_lints/duplication/releases.json`), the single source of those
  digests. No digest lives in this action.
- The detector binary is deliberately not cached between runs, so every run
  re-verifies the archive and no restored file can stand in for a verified one.
  The archive is a few megabytes.
- A `uv` already on `PATH` is kept: `setup-uv` runs only when none is found, so
  a caller's pinned `uv` stays the one every later step uses. When it does run,
  its cache is disabled, and the shared prune step runs before the first `uv`
  invocation.
- Linux and macOS runners only, as the package's `install` supports.

## Usage

```yaml
- name: Duplication gate
  uses: leynos/shared-actions/.github/actions/df12-duplication@<40-hex-commit-sha>
```
