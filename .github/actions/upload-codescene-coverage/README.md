# Upload CodeScene Coverage

Upload coverage reports to CodeScene and cache the CLI for faster runs.

## Inputs

| Name  | Description                                | Required | Default |
| ----- | ------------------------------------------ | -------- | ------- |
| path  | Coverage file path (set to `__auto__` to infer) | no       | `__auto__` |
| format | Coverage format (`cobertura` or `lcov`)     | no       | `cobertura` |

If `path` is left as `__auto__`, the action will look for `lcov.info` when
`format` is `lcov`, or `coverage.xml` when `format` is `cobertura`.

## Example

```yaml
- uses: ./.github/actions/upload-codescene-coverage@v1
```

See [CHANGELOG](CHANGELOG.md) for release history.
