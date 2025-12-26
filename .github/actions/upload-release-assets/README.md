# Upload Release Assets

Upload staged artefacts to a GitHub release or validate them in dry-run mode.

This action discovers artefacts in a staging directory, validates their
filenames and sizes, and uploads them using the GitHub CLI (`gh`). It supports
a dry-run mode for validating assets without mutating release state.

## Inputs

| Name | Description | Required | Default |
| ---- | ----------- | -------- | ------- |
| `release-tag` | Git tag identifying the release to publish to | yes | - |
| `bin-name` | Binary name used to derive artefact names | yes | - |
| `dist-dir` | Directory containing staged artefacts | no | `dist` |
| `dry-run` | When true, only validate artefacts and print the upload plan | no | `"false"` |
| `clobber` | Overwrite existing assets with the same name | no | `"true"` |

## Outputs

| Name | Description |
| ---- | ----------- |
| `uploaded-count` | Number of assets uploaded (or validated in dry-run mode) |
| `upload-error` | `"true"` or `"false"` indicating whether an error occurred |
| `error-message` | Summary of the error when `upload-error` is `"true"` |

## Usage

### Basic usage

```yaml
- uses: ./.github/actions/upload-release-assets
  with:
    release-tag: ${{ github.ref_name }}
    bin-name: myapp
```

### With dry-run validation

```yaml
- uses: ./.github/actions/upload-release-assets
  id: upload
  with:
    release-tag: ${{ github.ref_name }}
    bin-name: myapp
    dry-run: "true"

- name: Check results
  if: steps.upload.outputs.upload-error == 'true'
  run: |
    echo "Upload validation failed: ${{ steps.upload.outputs.error-message }}"
    exit 1
```

### Remote usage

```yaml
- uses: leynos/shared-actions/.github/actions/upload-release-assets@v1
  with:
    release-tag: v1.2.3
    bin-name: myapp
    dist-dir: dist/staging
```

## Asset Discovery

The action discovers the following artefact types within `dist-dir`:

| Pattern | Description |
| ------- | ----------- |
| `{bin-name}` | Linux/macOS binary |
| `{bin-name}.exe` | Windows executable |
| `{bin-name}.1` | Man page |
| `*.deb` | Debian package |
| `*.rpm` | RPM package |
| `*.pkg` | macOS installer package |
| `*.msi` | Windows installer |
| `*.sha256` | SHA-256 checksum sidecar files |

### Nested directories

Files in nested directories are namespaced with their path prefix, replacing
`/` with `__`. For example:

- `dist/linux/myapp` uploads as `linux-myapp`
- `dist/macos/arm64/myapp` uploads as `macos__arm64-myapp`

## Behaviour

1. **Discovery**: Recursively scan `dist-dir` for matching artefacts
2. **Validation**: Verify files are non-empty and have unique asset names
3. **Upload**: Use `gh release upload` to publish artefacts (or print plan in dry-run mode)

### Error Handling

The action sets `upload-error` to `"true"` when:

- The `dist-dir` directory does not exist
- No matching artefacts are found
- An artefact file is empty (0 bytes)
- Two files would upload with the same asset name
- The `gh` CLI fails during upload

## Requirements

- The `gh` CLI must be available in PATH (provided by GitHub-hosted runners)
- The workflow must have `contents: write` permission for uploads
- A GitHub release must already exist for the specified tag

## Release History

See [CHANGELOG](CHANGELOG.md).
