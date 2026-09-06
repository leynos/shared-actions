# Dependabot auto-merge reusable workflow

This workflow enables GitHub auto-merge for eligible Dependabot pull requests.
It is designed to be called from other repositories via `workflow_call` and
uses a Cyclopts-based Python helper that reads `INPUT_*` environment variables.

## Eligibility rules

The helper script only enables auto-merge when all of the following are true:

- The pull request author is `dependabot[bot]` or `dependabot`.
- Every commit on the branch was written by Dependabot.
- The pull request is not marked as a draft.
- The required label (default `dependencies`) is present.

If any rule fails, the workflow logs an `automerge_status=skipped` entry with a
reason and exits successfully.

### Why commit authorship is checked as well as the author

Opening a pull request is not the same as writing what is in it. Checking only
the author answers "did Dependabot open this?" when the question that decides
whether review can be skipped is "is everything in it a dependency bump?".

Once Dependabot opens a pull request, anything anyone pushes to that branch
would otherwise merge under the same rule, unreviewed. That is not theoretical:
a workflow change reached a trunk this way on 2026-09-05, in a bump whose title
and author were Dependabot's throughout.

A branch carrying a commit Dependabot did not write skips with
`automerge_reason=foreign-commit:<sha>`, and the run logs a notice naming each
such commit and its author. The remedy is to open that change as its own pull
request with its own review, not to remove the check.

Two deliberate limits. A commit co-authored by Dependabot and a human counts as
the human's, since a check that merely looked for Dependabot among the authors
would wave it through. And a commit list the API does not return counts as
nothing rather than as foreign: a query change that stopped returning commits
would otherwise halt every consumer's automerge at once, which is a worse
failure than the one this prevents.

Pushing a fix onto a Dependabot branch has a second cost worth knowing. Once a
branch has a non-Dependabot commit, Dependabot will no longer rebase or
recreate it, so the pull request has to be maintained by hand from then on.

Note: The helper reads `DEPENDABOT_LOGINS` (defined in
`workflow_scripts/dependabot_automerge.py`) to support both author login
variants across platforms.

## Merge-state behaviour

After author/draft/label checks pass, the helper evaluates GitHub merge state:

- `BLOCKED` arms auto-merge. This is the primary path: required status checks
  are still pending (or other required rules are unsatisfied), so GitHub merges
  the PR automatically once they pass.
- `CLEAN`, `HAS_HOOKS`, and `UNSTABLE` merge the PR directly. GitHub rejects
  `enablePullRequestAutoMerge` on an already-mergeable PR ("Pull request is in
  clean/unstable status"), so the helper performs the merge itself — matching
  what auto-merge would do, because all *required* rules are already satisfied.
- `DIRTY`, `BEHIND`, `MERGED`, and `CONFLICTING` are skipped.
- `UNKNOWN` mergeability is retried with backoff before a final decision.

## Prerequisite: required status checks

The target repository **must** protect its default branch with required status
checks (via a classic branch protection rule or a repository ruleset). Without
them, a freshly opened Dependabot PR is immediately mergeable while its CI is
still running (`UNSTABLE`), and merging it would bypass CI entirely. With
required checks configured, the PR stays `BLOCKED` until CI passes, auto-merge
can be armed, and GitHub only merges when the required checks succeed.

Auto-merge must also be enabled in the repository settings (**Settings →
General → Allow auto-merge**).

## Required permissions

The calling workflow must grant at least:

- `contents: write`
- `pull-requests: write`
- `checks: read`
- `statuses: read`
- `id-token: write` (required for GitHub OpenID Connect (OIDC) reusable workflow
  introspection)

Job-level permissions in the caller are authoritative and cannot be elevated by
this reusable workflow.

## Usage

```yaml
name: dependabot-automerge

# Uses pull_request_target to enable auto-merge with write permissions.
# Safe because the reusable workflow never checks out or executes PR code;
# it only reads event metadata and makes GitHub API calls.

on:
  pull_request_target:
    branches: [main]
    types: [opened, reopened, synchronize, labeled, ready_for_review]
  workflow_dispatch:
    inputs:
      pull-request-number:
        type: number
        required: true

jobs:
  automerge:
    permissions:
      contents: write
      pull-requests: write
      checks: read
      statuses: read
      # Needed for reusable workflow introspection via GitHub OpenID Connect (OIDC):
      # the called workflow uses an OIDC token to read `job_workflow_ref`/`job_workflow_sha`,
      # so it can checkout the *reusable workflow repo* (leynos/shared-actions) at the exact
      # pinned commit, rather than accidentally resolving to the caller repo via `github.workflow_*`.
      # The token is not used for any external cloud auth.
      id-token: write
    # The caller can filter on dependabot[bot] to reduce noise; the reusable
    # workflow still validates eligibility via DEPENDABOT_LOGINS.
    if: ${{ github.event_name == 'workflow_dispatch' || github.actor == 'dependabot[bot]' }}
    uses: leynos/shared-actions/.github/workflows/dependabot-automerge.yml@9d4c046f2788decc264847622b830e2a4d35b91f
    with:
      pull-request-number: ${{ inputs.pull-request-number || github.event.pull_request.number }}
```

## Inputs

- `required-label` (string, default: `dependencies`): label required to enable
  auto-merge. Set to an empty string to disable the label requirement.
- `merge-method` (string, default: `squash`): one of `squash`, `merge`,
  `rebase`.
- `dry-run` (boolean, default: `false`): if `true`, no API calls are made; the
  workflow logs the decision and exits.
- `pull-request-number` (number, recommended): the pull request number. When
  calling this workflow via `workflow_call`, the event context is the caller's
  `workflow_call` payload, not the original `pull_request` event. Pass
  `${{ github.event.pull_request.number }}` explicitly to ensure the PR number
  is available. The fallback to event parsing is only reliable for direct
  `pull_request` triggers.
- `repository` (string, optional): override the `owner/repo` when the event
  payload or `GITHUB_REPOSITORY` are unavailable.

## Secrets

- `github-token` (optional): token with the required permissions. If omitted,
  the workflow uses `github.token`.

## Local validation

To exercise the workflow locally, run the act harness with the provided
fixture. The integration test in
`tests/workflows/test_dependabot_automerge_workflow.py` uses
`ACT_WORKFLOW_TESTS=1` and the `pull_request_dependabot.event.json` fixture in
`tests/workflows/fixtures/`.

## Notes

- Auto-merge must be enabled for the target repository. If the repo disables
  auto-merge, the workflow will fail with a clear error message.
- Branch protection rules still apply; auto-merge will wait for required checks
  to pass.
