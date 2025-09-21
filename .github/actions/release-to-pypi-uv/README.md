# Release to PyPI (uv)

Build and publish Python distributions via
[uv](https://github.com/astral-sh/uv) with GitHub's trusted publishing flow.

## Inputs

| Name | Description | Required | Default |
| --- | --- | --- | --- |
| tag | Tag to release (e.g. `v1.2.3`). Required when the workflow is not running on a tag ref. | no | _(empty)_ |
| require-confirmation | Require a manual confirmation string before publishing. | no | `false` |
| confirm | Confirmation string. Must equal `release <tag>` when `require-confirmation` is true. | no | _(empty)_ |
| environment-name | GitHub environment to reference in the release summary. | no | `pypi` |
| uv-index | Optional uv index name to publish to (e.g. `testpypi`). Must exist in `tool.uv.index`. | no | _(empty)_ |
| toml-glob | Glob used to discover `pyproject.toml` files for version validation. | no | `**/pyproject.toml` |
| fail-on-dynamic-version | Fail when a project declares a dynamic PEP 621 version instead of a literal string. | no | `false` |
| python-version | Python version to install and use for all uv commands. | no | `3.13` |

The composite action installs the interpreter requested through `python-version`
before invoking any uv commands, ensuring builds run against the expected
runtime.

## Outputs

| Name | Description |
| --- | --- |
| tag | Resolved release tag. |
| version | Resolved release version (tag without the leading `v`). |

> **Required permissions**: set the job to `permissions: contents: read` and `permissions: id-token: write` so uv Trusted Publishing can exchange an OIDC token with PyPI.
> The composite action forwards the workflow's `GITHUB_TOKEN` to its scripts as `GH_TOKEN`, so you do not need to add an extra `env` block.

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
