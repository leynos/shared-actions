# ensure-cargo-version

Validate that the Git tag triggering a release workflow matches the version in one or more Cargo manifests.

## Inputs

| Name | Required | Default | Description |
| ---- | -------- | ------- | ----------- |
| `manifests` | No | `Cargo.toml` | Newline or whitespace separated list of Cargo manifest paths to check. Paths are resolved relative to the GitHub workspace. |
| `tag-prefix` | No | `v` | Prefix stripped from the Git reference name before comparing against manifest versions. Use an empty string to disable prefix removal. |
| `check-tag` | No | `true` | Disable tag comparison by supplying a falsey value (case-insensitive `false`, `0`, `no`, `off`, or an empty string). Truthy values (`true`, `1`, `yes`, `on`) enable comparison while still attempting to read the tag for output purposes. |

## Outputs

| Name | Description |
| ---- | ----------- |
| `version` | Version extracted from the tag reference after removing the configured prefix. |
| `crate-version` | Version read from the first manifest path provided (after resolution) after resolving workspace inheritance. |

## Usage

```yaml
on:
  push:
    tags:
      - "v*"
      - "*-v*"

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Ensure Cargo versions match tag
        uses: ./.github/actions/ensure-cargo-version
        with:
          manifests: |
            Cargo.toml
            crates/secondary/Cargo.toml
          # If your tags are namespaced (e.g. "ensure-cargo-version-v1.2.3"),
          # set the tag prefix so the extracted version is "1.2.3":
          # tag-prefix: ensure-cargo-version-v

      - name: Read crate version without enforcing tag match
        uses: ./.github/actions/ensure-cargo-version
        with:
          manifests: Cargo.toml
          check-tag: "false"
```

## Notes

- **Namespaced tags**: The action strips a leading `tag-prefix` (default `v`).
  For tags like `my-action-v1.2.3`, set `tag-prefix: my-action-v`.
- **Workspace-inherited versions**: When a crate sets `version.workspace = true`,
  the action resolves the version from the workspace root manifest's
  `[workspace.package].version` entry.
- **uv provisioning**: The composite action runs `astral-sh/setup-uv` and
  provisions Python 3.13 via `uv python install`, so downstream workflows
  do not need a separate `uv` installation step. This addresses the review
  request to clarify how `uv` becomes available.
- **Failure behaviour**: Any parse error or version mismatch emits GitHub
  Actions `::error` annotations and exits with status `1`, failing the job.
- **Optional tag validation**: If `check-tag` is set to `false`, the action still
  reads and outputs the manifest version without enforcing a match against the
  tag-derived version. The script keeps attempting to read the tag to emit the
  `version` output; when no tag reference is available, the `version` output is
  omitted while `crate-version` remains populated.
