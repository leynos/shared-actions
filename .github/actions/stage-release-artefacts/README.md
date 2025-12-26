# Stage Release Artefacts

Stage release artefacts using a TOML configuration file.

This action copies build artefacts into a staging directory, generates checksum
sidecar files, and exports paths and metadata as workflow outputs. It supports
glob patterns, template variables, and optional artefacts.

## Inputs

| Name | Description | Required | Default |
| ---- | ----------- | -------- | ------- |
| `config-file` | Path to the TOML staging configuration file | yes | - |
| `target` | Target key from the configuration file | yes | - |
| `normalize-windows-paths` | Convert backslashes to forward slashes in outputs | no | `"false"` |

## Outputs

| Name | Description |
| ---- | ----------- |
| `artifact-dir` | Absolute path to the directory containing staged artefacts |
| `dist-dir` | Absolute path to the parent distribution directory |
| `staged-files` | Newline-separated list of staged file names |
| `artefact-map` | JSON map of named outputs to their absolute paths |
| `checksum-map` | JSON map of file names to checksum digests |
| `binary-path` | Absolute path to the staged binary (when configured) |
| `man-path` | Absolute path to the staged man page (when configured) |
| `license-path` | Absolute path to the staged license file (when configured) |

## Usage

### Basic usage

```yaml
- uses: ./.github/actions/stage-release-artefacts
  id: stage
  with:
    config-file: .github/release-staging.toml
    target: linux-x86_64

- name: Show staged files
  run: echo "${{ steps.stage.outputs.staged-files }}"
```

### Remote usage

```yaml
- uses: leynos/shared-actions/.github/actions/stage-release-artefacts@v1
  id: stage
  with:
    config-file: .github/release-staging.toml
    target: windows-x86_64
    normalize-windows-paths: "true"
```

## Configuration File Format

The staging configuration is a TOML file with `[common]` and `[targets.*]`
sections:

```toml
[common]
bin_name = "myapp"
dist_dir = "dist"
checksum_algorithm = "sha256"
staging_dir_template = "{bin_name}_{platform}_{arch}"

[[common.artefacts]]
source = "LICENSE"
destination = "LICENSE"
output = "license_path"
required = true

[[common.artefacts]]
source = "target/{target}/release/{bin_name}{bin_ext}"
output = "binary_path"

[targets.linux-x86_64]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"

[targets.windows-x86_64]
platform = "windows"
arch = "x86_64"
target = "x86_64-pc-windows-msvc"
bin_ext = ".exe"
```

### Template Variables

The following variables are available in `source`, `destination`, and
`staging_dir_template`:

| Variable | Description |
| -------- | ----------- |
| `{workspace}` | GitHub workspace path |
| `{bin_name}` | Binary name from config |
| `{dist_dir}` | Distribution directory |
| `{platform}` | Platform identifier (linux, windows, macos) |
| `{arch}` | Architecture identifier (x86_64, aarch64) |
| `{target}` | Rust target triple |
| `{bin_ext}` | Binary extension (.exe on Windows) |
| `{target_key}` | The target key passed to the action |
| `{source_name}` | Source file name (in destination templates) |
| `{source_path}` | Full source path (in destination templates) |

### Artefact Options

| Key | Type | Default | Description |
| --- | ---- | ------- | ----------- |
| `source` | string | (required) | Path pattern to source file (supports globs) |
| `destination` | string | `{source_name}` | Target filename in staging directory |
| `output` | string | - | GitHub output key to export the staged path |
| `required` | bool | `true` | Whether missing source is an error |
| `alternatives` | list | `[]` | Fallback patterns if source not found |

## Behaviour

1. **Directory setup**: Creates a clean staging directory (removes existing)
2. **Artefact resolution**: For each configured artefact:
   - Renders path templates with configuration context
   - Matches glob patterns or direct paths
   - Falls back to alternatives if primary source missing
3. **Staging**: Copies matched files to the staging directory
4. **Checksums**: Generates `.sha256` sidecar files for each staged artefact
5. **Output**: Exports paths and metadata to `GITHUB_OUTPUT`

### Path Resolution

- Relative paths are resolved from `GITHUB_WORKSPACE`
- Glob patterns select the newest matching file (by modification time)
- Windows paths are supported and normalized when using `normalize-windows-paths`

### Error Handling

The action fails when:
- Configuration file is missing or invalid
- Required artefact sources are not found
- Template variables reference undefined keys
- Destination paths escape the staging directory

## Release History

See [CHANGELOG](CHANGELOG.md).
