# Upload CodeScene Coverage

Upload coverage reports to CodeScene and cache the CLI for faster runs.

## Inputs

| Name             | Description                                  | Required | Default |
| ---------------- | -------------------------------------------- | -------- | ------- |
| path             | Coverage file path; use `__auto__` to infer   | no       | `__auto__` |
| format           | Coverage format (`cobertura` or `lcov`)       | no       | `cobertura` |
| access-token     | CodeScene project access token                | yes      |         |
| installer-checksum | SHA-256 checksum of the installer script    | no       |         |

If `path` is left as `__auto__`, the action will look for `lcov.info` when
`format` is `lcov`, or `coverage.xml` when `format` is `cobertura`.
The CodeScene CLI is cached using its release version extracted from the
installer script. If the optional `installer-checksum` input is set,
the installer is validated before execution. Any other value for
`format` results in an error.

The provided `access-token` and `installer-checksum` inputs become the
`CS_ACCESS_TOKEN` and `CODESCENE_CLI_SHA256` environment variables
available to steps within the action.

## Outputs

None

## Example

```yaml
- uses: ./.github/actions/upload-codescene-coverage@v1
  with:
    path: coverage.xml
    format: cobertura
    access-token: ${{ secrets.CS_ACCESS_TOKEN }}
    installer-checksum: ${{ vars.CODESCENE_CLI_SHA256 }}
```

## Caching

The CodeScene Coverage CLI is stored in `~/.local/bin/cs-coverage` and cached
with [actions/cache](https://github.com/actions/cache). The cache key combines
the runner OS and the CLI version extracted from the installer script. The cache
is restored at the start of the job and saved after the job finishes. A fallback
restore key allows reuse across patch releases:

```yaml
uses: actions/cache@v4
with:
  path: ~/.local/bin/cs-coverage
  key: ${{ runner.os }}-cs-coverage-${{ version }}
  restore-keys: |
    ${{ runner.os }}-cs-coverage-${{ major_minor }}
```

### Requirements

- Provide an `access-token` so the installer can download the CLI and
  authenticate uploads.
- Set `installer-checksum` to the published SHA-256 checksum to guard against
  tampering (optional).

### Extent and limitations

- GitHub limits each cache to 5 GB per operating system; old entries may be
  evicted as new ones are created.
- Caches are scoped to the runner OS, so Windows, macOS, and Linux caches are
  independent.
- If the CLI version changes or no cache entry exists, the installer runs again
  and a new cache entry is created.

### Effective use

- Pin the installer checksum whenever possible to avoid using a compromised
  download.
- Keep your coverage file path consistent across jobs so subsequent steps can
  locate it reliably.

Release history is available in [CHANGELOG](CHANGELOG.md).

