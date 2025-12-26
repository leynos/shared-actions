# Determine Release Modes

Normalize GitHub Actions event payloads into release workflow decision flags.

This action determines whether a workflow is a dry run, should publish to a
release, and should upload workflow artifacts based on the triggering event
type and optional input overrides.

## Inputs

| Name | Description | Required | Default |
| ---- | ----------- | -------- | ------- |
| `dry-run` | Override dry-run mode (auto-detected from event when empty) | no | `""` |
| `publish` | Override publish flag (auto-detected from event when empty) | no | `""` |

## Outputs

| Name | Description |
| ---- | ----------- |
| `dry-run` | `"true"` or `"false"` indicating dry-run mode |
| `should-publish` | `"true"` or `"false"` indicating whether to publish to a release |
| `should-upload-workflow-artifacts` | `"true"` or `"false"` indicating whether to upload workflow artifacts |

## Usage

### Basic usage (auto-detect from event)

```yaml
- uses: ./.github/actions/determine-release-modes
  id: modes

- name: Publish release
  if: steps.modes.outputs.should-publish == 'true'
  run: echo "Publishing..."
```

### With explicit overrides

```yaml
- uses: ./.github/actions/determine-release-modes
  id: modes
  with:
    dry-run: "true"
    publish: "false"
```

### Remote usage

```yaml
- uses: leynos/shared-actions/.github/actions/determine-release-modes@v1
  id: modes
```

## Behaviour

The action derives modes based on the GitHub event type:

| Event | Default dry-run | Default publish | Artifacts |
|-------|-----------------|-----------------|-----------|
| `push` (tag) | `false` | `true` | Uploaded |
| `workflow_call` | From inputs | From inputs | If not dry-run |
| `pull_request` | `true` | `false` | None |

### Rules

1. **Tag pushes** (`push` event): Always publish and upload artifacts unless
   explicitly overridden.

2. **Workflow calls** (`workflow_call`): Respect the caller's `dry-run` and
   `publish` inputs. Default to non-dry-run with no publishing.

3. **Pull requests** (`pull_request`): Default to dry-run mode for safety.
   Publishing and artifact uploads are disabled.

4. **Dry-run override**: When dry-run is enabled (explicitly or by default),
   publishing is automatically disabled regardless of the `publish` input.

5. **Artifact uploads**: Artifacts are uploaded whenever `dry-run` is `false`.

## Release History

See [CHANGELOG](CHANGELOG.md).
