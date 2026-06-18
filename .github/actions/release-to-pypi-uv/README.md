# Release to PyPI (uv)

Build and publish Python distributions via
[uv](https://github.com/astral-sh/uv) with GitHub's trusted publishing flow.

## Inputs

| Name | Description | Required | Default |
| --- | --- | --- | --- |
| tag | Tag to release (e.g. `v1.2.3`) | no | _(empty)_ |
| require-confirmation | Require manual confirmation | no | `false` |
| confirm | Confirmation string | no | _(empty)_ |
| environment-name | GitHub environment name | no | `pypi` |
| uv-index | uv index name (e.g. `testpypi`) | no | _(empty)_ |
| toml-glob | Glob for `pyproject.toml` discovery | no | `**/pyproject.toml` |
| skip-directories | Directories to skip | no | _(empty)_ |
| fail-on-dynamic-version | Fail on dynamic PEP 621 version | no | `false` |
| fail-on-empty | Fail on empty discovery | no | `false` |
| python-version | Python version | no | `3.13` |

The composite action installs the interpreter requested through `python-version`
before invoking any uv commands, ensuring builds run against the expected
runtime. Set `fail-on-empty: true` when your repository must always contain at
least one `pyproject.toml`. This turns the default warning into a failing error
so misconfigured globs surface early.

Directories named `.venv`, `venv`, `.mypy_cache`, `.pytest_cache`, `.cache`,
`htmlcov`, and `node_modules` are skipped during TOML discovery. Provide a
comma- or newline-separated list via `skip-directories` when your repository
uses additional transient paths that should be excluded.

## Outputs

| Name | Description |
| --- | --- |
| tag | Resolved release tag. |
| version | Resolved release version (tag without the leading `v`). |

> **Required permissions**: set the job to `permissions: contents: read` and
> `permissions: id-token: write` so uv Trusted Publishing can exchange an
> OpenID Connect (OIDC) token with PyPI.
> The composite action forwards the workflow's `GITHUB_TOKEN` to its scripts
> as `GH_TOKEN`, so you do not need to add an extra `env` block.

## Usage

```yaml
name: Release
on:
  push:
    tags:
      - "v*"

jobs:
  publish:
    concurrency:
      group: release-pypi-${{ github.repository }}-${{ github.ref_name }}
      cancel-in-progress: true
    runs-on: ubuntu-latest
    permissions:
      contents: read        # required for trusted publishing
      id-token: write       # required for trusted publishing
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Build and publish
        uses: ./.github/actions/release-to-pypi-uv
        with:
          python-version: '3.12'
          require-confirmation: true
          confirm: release ${{ github.ref_name }}
```

Release history is available in [CHANGELOG](CHANGELOG.md).
