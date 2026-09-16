# Upload CodeScene Coverage

Upload coverage reports to CodeScene and cache the CLI for faster runs.

## Inputs

| Name               | Description                                                   | Required | Default     |
| ------------------ | ------------------------------------------------------------- | -------- | ----------- |
| path               | Coverage file path; blank or `__auto__` infers automatically  | no       | `__auto__`  |
| format             | Coverage format (`cobertura` or `lcov`)                       | no       | `cobertura` |
| access-token       | CodeScene project access token                                | yes      |             |
| installer-checksum | SHA-256 of the installer script; empty uses the pinned digest | no       | pinned      |
| cli-version        | cs-coverage build to install; empty uses the pinned build     | no       | pinned      |
| mode               | `upload` (analysed branches) or `check` (PR coverage gate)    | no       | `upload`    |
| project-url        | CodeScene project API URL; required when `mode` is `check`    | no       |             |

If `path` is empty or `__auto__`, the action looks for `lcov.info` when
`format` is `lcov`, or `coverage.xml` when `format` is `cobertura`. Any other
value for `format` results in an error.

The action exports the `access-token` and `installer-checksum` inputs as
`CS_ACCESS_TOKEN` and `CODESCENE_CLI_SHA256` for use by later steps.

## Pinning the CLI

CodeScene publishes the coverage CLI from a bucket that serves only `latest`
and one artefact per build commit SHA; release numbers such as `1.0.103` are
not downloadable. It also rewrites the installer script in place. An unverified
`latest` install therefore changes the coverage gate's verdict with no commit
anywhere in the consuming repository, which is what happened on 2026-09-16 when
a release stopped parsing Cobertura reports and failed every changed-line gate
with `No matching field found: close for class java.io.InputStreamReader`.

This action pins both halves:

- `cli-version` defaults to the pinned build SHA, which names one
  immutable artefact. Pass `latest` to float deliberately, or another build SHA
  to move ahead of the pin. Read a build's SHA from `cs-coverage version`,
  which prints it in parentheses after the release number.
- `installer-checksum` defaults to the pinned installer digest. An empty
  value falls back to that digest rather than switching verification off,
  because consumers pass the input from a repository variable that is usually
  unset. The action refuses to install when no digest is available from either
  source.

Moving the pin means recording the new build SHA and the installer digest
together, in one commit, with the reason.

## Modes

`upload` sends the report to CodeScene for an analysed branch; CodeScene
rejects uploads for branches the project does not analyse (typically anything
but `main`), so it belongs in push/main workflows. `check` runs the
pull-request changed-line coverage gate (`cs-coverage check`), which diffs the
PR against its merge base — the job must check out with `fetch-depth: 0`, pass
`project-url` (`https://api.codescene.io/v2/projects/<id>`), and, for LCOV,
name the report file `*.info` because the CLI infers the format from the file
extension. When a pull request targets a branch other than the repository's
default branch, the action skips this gate with a warning because CodeScene has
no uploaded baseline for that merge base. If the CLI fails, the action prints
its verbose diagnostic and preserves its exit status instead of referring to
logs that are not exposed by the workflow.

## Environment variables

- `CS_ACCESS_TOKEN` – CodeScene project access token (required)
- `CODESCENE_CLI_SHA256` – SHA‑256 checksum for the installer; when empty,
  the action verifies against its own pinned digest instead

## Outputs

None

## Example

```yaml
- uses: ./.github/actions/upload-codescene-coverage@v1
  with:
    path: coverage.xml
    format: cobertura
    access-token: ${{ secrets.CS_ACCESS_TOKEN }}
```

## Caching

The CodeScene Coverage CLI is stored in `~/.local/bin/cs-coverage` and cached
with [actions/cache](https://github.com/actions/cache). The cache key combines
the runner OS and the resolved CLI build. The cache is restored at the start of
the job and saved after the job finishes. There is no fallback restore key: a
prefix match would return a different build and silently defeat the pin.

```yaml
uses: actions/cache@v4
with:
  path: ~/.local/bin/cs-coverage
  key: cs-coverage-cache-${{ runner.os }}-${{ version }}
```

### Requirements

- Provide an `access-token` so the installer can download the CLI and
  authenticate uploads.
- Set `installer-checksum` only to move ahead of the action's pinned installer
  digest; leaving it unset keeps verification on.

### Extent and limitations

- GitHub limits each cache to 5 GB per operating system; old entries may be
  evicted as new ones are created.
- Caches are scoped to the runner OS, so Windows, macOS, and Linux caches are
  independent.
- If the pinned build moves or no cache entry exists, the installer runs again
  and a new cache entry is created.
- `cli-version: latest` skips the cache entirely, so a floating build is never
  stored under a key that would outlive it.

### Effective use

- Leave `installer-checksum` and `cli-version` unset so the action's own pins
  apply; override them only to move ahead of a pin deliberately.
- Keep your coverage file path consistent across jobs so subsequent steps can
  locate it reliably.

Release history is available in [CHANGELOG](CHANGELOG.md).
