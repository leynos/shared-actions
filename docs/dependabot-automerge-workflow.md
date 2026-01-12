# Dependabot auto-merge reusable workflow

This workflow enables GitHub auto-merge for eligible Dependabot pull requests.
It is designed to be called from other repositories via `workflow_call` and
uses a Cyclopts-based Python helper that reads `INPUT_*` environment variables.

## Eligibility rules

The helper script only enables auto-merge when all of the following are true:

- The pull request author is `dependabot[bot]`.
- The pull request is not marked as a draft.
- The required label (default `dependencies`) is present.

If any rule fails, the workflow logs a `automerge_status=skipped` entry with a
reason and exits successfully.

## Required permissions

The calling workflow must grant at least:

- `contents: write`
- `pull-requests: write`
- `checks: read`
- `statuses: read`

Job-level permissions in the caller are authoritative and cannot be elevated by
this reusable workflow.

## Usage

```yaml
name: Auto-merge Dependabot PRs

on:
  pull_request:
    types: [opened, reopened, synchronize, labeled, ready_for_review]

jobs:
  automerge:
    permissions:
      contents: write
      pull-requests: write
      checks: read
      statuses: read
    uses: your-org/shared-actions/.github/workflows/dependabot-automerge.yml@v1
    with:
      required-label: dependencies
      merge-method: squash
    secrets:
      github-token: ${{ secrets.DEPENDABOT_AUTOMERGE_TOKEN }}
```

## Inputs

- `required-label` (string, default: `dependencies`): label required to enable
  auto-merge. Set to an empty string to disable the label requirement.
- `merge-method` (string, default: `squash`): one of `squash`, `merge`, `rebase`.
- `dry-run` (boolean, default: `false`): if `true`, no API calls are made; the
  workflow logs the decision and exits.
- `pull-request-number` (number, optional): override the PR number if the event
  payload is not a pull request.
- `repository` (string, optional): override the `owner/repo` when the event
  payload or `GITHUB_REPOSITORY` are unavailable.

## Secrets

- `github-token` (optional): token with the required permissions. If omitted,
  the workflow uses `github.token`.

## Local validation

To exercise the workflow locally, run the act harness with the provided
fixture. The integration test in `tests/workflows/test_dependabot_automerge_workflow.py`
uses `ACT_WORKFLOW_TESTS=1` and the `pull_request_dependabot.event.json` fixture
in `tests/workflows/fixtures/`.

## Notes

- Auto-merge must be enabled for the target repository. If the repo disables
  auto-merge, the workflow will fail with a clear error message.
- Branch protection rules still apply; auto-merge will wait for required checks
  to pass.
