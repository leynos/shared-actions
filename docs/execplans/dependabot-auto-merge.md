# Dependabot auto-merge reusable workflow (plan)

This ExecPlan is a living document. The sections `Constraints`, `Tolerances`,
`Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: COMPLETE

PLANS.md: none found in this repo.

## Purpose / Big Picture

Create a reusable workflow that enables safe, auditable auto-merge of
Dependabot pull requests, with a small Python script (Cyclopts + env vars) that
handles gating rules and the GitHub API calls. Success looks like a caller
workflow being able to reference a single workflow file in this repo and have
Dependabot PRs auto-merged once policy gates are satisfied, with a deterministic
local validation path via `pytest` + `act`.

## Constraints

- Follow `docs/scripting-standards.md` for all new scripts (Python 3.13, `uv`
  script block, Cyclopts env-first config, pathlib usage, and no ad-hoc shell
  parsing).
- Follow `docs/local-validation-of-github-actions-with-act-and-pytest.md` for
  workflow integration tests (black-box, `act --json`, artefacts/log assertions).
- Pin all third-party actions to a full commit SHA.
- Use least-privilege workflow permissions and avoid elevating permissions in
  callers (document required permissions explicitly).
- Do not introduce shell `eval` or unvalidated user input execution.
- Keep existing action interfaces stable; only add new files/sections needed
  for the reusable workflow.

## Tolerances (Exception Triggers)

- Scope: if implementation requires changes to more than 12 files or more than
  900 net new lines, stop and escalate.
- Dependencies: if a new external dependency (not already in `pyproject.toml`)
  is required, stop and escalate.
- Interface: if existing public action interfaces must change, stop and
  escalate.
- Iterations: if tests still fail after two full fix attempts, stop and
  escalate with the failures and hypotheses.
- Ambiguity: if GitHub API behaviour or workflow-call semantics appear unclear
  in practice, stop and ask before proceeding.

## Risks

- Risk: The workflow could merge a PR that is not authored by Dependabot if the
  event payload differs from expectations.
  Severity: high
  Likelihood: low
  Mitigation: check `pull_request.user.login` in the event payload and re-check
  via API before enabling auto-merge.

- Risk: Auto-merge enablement fails due to missing permissions or repo settings.
  Severity: medium
  Likelihood: medium
  Mitigation: detect and log explicit errors; surface a clear, non-zero exit
  with remediation instructions; document required permissions in README/docs.

- Risk: Local `act` tests diverge from GitHub runner behaviour.
  Severity: medium
  Likelihood: medium
  Mitigation: keep `act` tests in dry-run mode and document that GitHub runner
  validation is authoritative.

## Progress

- [x] (2026-01-12 00:00Z) Drafted initial ExecPlan.
- [x] (2026-01-12 00:00Z) Received approval to proceed with implementation.
- [x] (2026-01-12 00:00Z) Implement reusable workflow and helper script.
- [x] (2026-01-12 00:00Z) Add unit tests for the helper script.
- [x] (2026-01-12 00:00Z) Add workflow integration test via `act` harness.
- [x] (2026-01-12 00:00Z) Update documentation with usage guidance.
- [x] (2026-01-12 00:00Z) Reordered dry-run validation to require event payload before PR number resolution.
- [x] (2026-01-12 00:00Z) Adjusted linux-packages worktree repo-root test to accept `.git` files.
- [x] (2026-01-12 00:00Z) Ran format, lint, typecheck, and test gates.
- [x] (2026-01-12 00:00Z) Committed implementation changes.

## Surprises & Discoveries

- Observation: Creating a top-level `scripts/` package conflicts with action
  test imports that rely on the `scripts` module name.
  Evidence: `make test` errors in release-to-pypi-uv and validate-linux-packages
  when `scripts` resolved to the new root package.
  Impact: Move the helper script to a distinct `workflow_scripts/` package.

- Observation: Worktree checkouts use a `.git` file instead of a `.git` directory,
  causing repo-root detection to fail in linux-packages tests.
  Evidence: `make test` failure in
  `.github/actions/linux-packages/tests/test_action_workdir.py` when `.git` was
  a file.
  Impact: Broaden repo-root detection to accept `.git` files.

- Observation: `act` runs failed with `setup-uv` under Node 18 and with pip's
  PEP 668 protections when installing uv.
  Evidence: `ACT_WORKFLOW_TESTS=1 make test` failures in the reusable workflow
  job during the `setup-uv` and `pip install uv` steps.
  Impact: Use act-specific installation (pip with `--break-system-packages`),
  and redirect uv's environment/cache to `/tmp` during act runs.

## Decision Log

- Decision: Implement auto-merge enablement via GitHub GraphQL API
  (`enablePullRequestAutoMerge`) rather than immediate merge.
  Rationale: GraphQL auto-merge defers the merge until checks pass and branch
  protections are satisfied, reducing race conditions and simplifying check
  evaluation logic.
  Date/Author: 2026-01-12 (assistant).

- Decision: Provide a dry-run mode for tests and local validation.
  Rationale: avoids network dependency in `act`/pytest while still exercising
  workflow wiring and logic paths.
  Date/Author: 2026-01-12 (assistant).

- Decision: Checkout the reusable workflow repository at runtime using
  `github.workflow_ref` so the job can execute the versioned helper script.
  Rationale: the called workflow does not have the shared repo on disk by
  default, so the job must fetch the matching ref to ensure script parity.
  Date/Author: 2026-01-12 (assistant).

- Decision: Store the helper script under `workflow_scripts/` instead of a
  top-level `scripts/` package.
  Rationale: avoids shadowing action-level `scripts` modules used in existing
  tests and keeps action imports stable.
  Date/Author: 2026-01-12 (assistant).

- Decision: Add `workflow_scripts/tests` to pytest discovery.
  Rationale: ensures the new helper script unit tests run under the default
  `make test` target.
  Date/Author: 2026-01-12 (assistant).

- Decision: Treat a `.git` file as a valid repo-root indicator in worktree
  environments.
  Rationale: git worktrees store metadata in a file, and tests should pass in
  that layout.
  Date/Author: 2026-01-12 (assistant).

- Decision: Add act-only workflow branches to skip `setup-uv`, install uv via
  pip with `--break-system-packages`, and redirect uv paths to `/tmp`.
  Rationale: act uses Node 18 and Debian PEP 668 defaults; this keeps workflow
  tests green without affecting GitHub runner behaviour.
  Date/Author: 2026-01-12 (assistant).

## Outcomes & Retrospective

- Delivered a reusable Dependabot auto-merge workflow, a Cyclopts-based helper
  script with dry-run support, unit tests, and `act`-driven workflow tests, plus
  documentation and README updates.
- Quality gates completed: `make fmt`, `make check-fmt`, `make lint`,
  `make typecheck`, and `make test` passed (typecheck emitted an existing
  warning in `.github/actions/windows-package/scripts/generate_wxs.py`).
- Learned: worktree environments surface `.git` as a file, so repo-root probes
  should accept both file and directory forms.

## Context and Orientation

Relevant repository locations:

- `.github/workflows/`: location for reusable workflows and workflow tests.
- `tests/workflows/`: existing `act`-based integration harness and fixtures.
- `docs/scripting-standards.md`: defines the Cyclopts + `uv` script pattern.
- `docs/local-validation-of-github-actions-with-act-and-pytest.md`: defines the
  `act` harness and how to test workflows locally.

This change adds a new reusable workflow file under `.github/workflows/` and a
new Python script under `workflow_scripts/` to implement the auto-merge logic.
Tests will be added under `workflow_scripts/tests/` for unit coverage and under
`tests/workflows/` for black-box workflow validation.

Key terms used here:

- Reusable workflow: a workflow declared with `on: workflow_call` so other
  repositories can call it via `jobs.<id>.uses`.
- Auto-merge: GitHub's built-in feature that merges once checks and protections
  are satisfied.

## Plan of Work

Stage A: confirm requirements and align on interfaces (no code changes).

- Review existing workflow conventions in `.github/workflows/` and confirm the
  pinned-action SHA pattern for new workflows.
- Validate the desired inputs for the reusable workflow (label gate, merge
  method, dry-run toggle, optional PR number override).
- Confirm the decision to use GraphQL auto-merge and the required permissions
  (`contents: write`, `pull-requests: write`, `checks: read`, `statuses: read`).

Stage B: scaffold script and unit tests (small, verifiable diffs).

- Add `workflow_scripts/dependabot_automerge.py` with a `uv` script block
  targeting Python 3.13, using Cyclopts env-first config
  (`Env("INPUT_", command=False)`).
- Implement structured logging (JSON or key-value lines) so workflow tests can
  assert on logs.
- Add unit tests in `workflow_scripts/tests/test_dependabot_automerge.py` using
  `cyclopts.testing.invoke` plus `monkeypatch`/`pytest-mock` to stub HTTP calls.
- Ensure graceful error messages and exit codes as per scripting standards.

Stage C: create reusable workflow and integration test harness.

- Add `.github/workflows/dependabot-automerge.yml` with `on: workflow_call` and
  job-level permissions pinned to least privilege.
- Add `.github/workflows/test-dependabot-automerge.yml` that exercises the
  reusable workflow in dry-run mode for `act`.
- Add `tests/workflows/test_dependabot_automerge_workflow.py` and a fixture
  event JSON (for a Dependabot-authored PR) to validate the workflow output
  logs using the existing `run_act` harness.

Stage D: documentation and cleanup.

- Update `README.md` to add a small “Reusable workflows” section or mention
  the new workflow alongside existing actions.
- Add a short usage guide (likely `docs/dependabot-automerge-workflow.md`)
  with copy-paste example and required permissions.
- Ensure all new files follow repository formatting and lint rules.

Each stage ends with validation; do not proceed if validation fails.

## Concrete Steps

All commands run from repository root: `/data/leynos/Projects/shared-actions.worktrees/dependabot-auto-merge`.

1) Create the script and unit tests.

    - Create `workflow_scripts/dependabot_automerge.py`.
    - Create `workflow_scripts/tests/test_dependabot_automerge.py`.

2) Add workflows and fixtures.

    - Add `.github/workflows/dependabot-automerge.yml`.
    - Add `.github/workflows/test-dependabot-automerge.yml`.
    - Add `tests/workflows/fixtures/pull_request_dependabot.event.json`.
    - Add `tests/workflows/test_dependabot_automerge_workflow.py`.

3) Update documentation.

    - Update `README.md` with the reusable workflow entry.
    - Add `docs/dependabot-automerge-workflow.md` with usage and permissions.

4) Run quality gates (per AGENTS.md) with tee logging.

    - `make check-fmt 2>&1 | tee /tmp/check-fmt.log`
    - `make typecheck 2>&1 | tee /tmp/typecheck.log`
    - `make lint 2>&1 | tee /tmp/lint.log`
    - `make test 2>&1 | tee /tmp/test.log`

    If `act` tests are required and Docker/Podman needs sudo:

    - `ACT_WORKFLOW_TESTS=1 sudo -E make test 2>&1 | tee /tmp/test-act.log`

5) Commit in small steps, gating each commit with relevant checks.

## Validation and Acceptance

Behavioural acceptance:

- A consuming repo can call the workflow via `jobs.<id>.uses` and see the job
  skip non-Dependabot PRs, skip drafts, and enable auto-merge for Dependabot PRs
  when label requirements are met.
- Logs include deterministic markers (e.g. `automerge_status=enabled` or
  `automerge_status=skipped`) that tests can assert on.

Quality criteria:

- Tests: `make test` passes; `ACT_WORKFLOW_TESTS=1` optional run passes when the
  container runtime is available.
- Lint/typecheck/format: `make check-fmt`, `make typecheck`, `make lint` pass.
- Security: actions pinned to SHAs, minimal permissions documented.

## Idempotence and Recovery

- The script will be idempotent: enabling auto-merge on an already-enabled PR
  should detect the existing state and exit successfully without changes.
- Dry-run mode should avoid any network calls and still emit deterministic logs.
- If the workflow fails due to permissions, rerun after updating permissions;
  no state changes are left behind by the script unless auto-merge was enabled.

## Artifacts and Notes

Expected log snippets (examples to align tests):

    automerge_status=enabled
    automerge_reason=label:dependencies

Or for skips:

    automerge_status=skipped
    automerge_reason=author-not-dependabot

## Interfaces and Dependencies

Script: `workflow_scripts/dependabot_automerge.py`

- CLI: Cyclopts app with env-first config (`Env("INPUT_", command=False)`).
- Inputs (env or CLI):
  - `github_token` (required): token with `pull-requests: write`.
  - `merge_method` (optional): `squash|merge|rebase` (default `squash`).
  - `required_label` (optional): default `dependencies`.
  - `dry_run` (optional): boolean, default `false`.
  - `pull_request_number` (optional): override; otherwise parse from event.
  - `repository` (optional): override; otherwise use `GITHUB_REPOSITORY`.
- Behaviour:
  - Load event payload from `GITHUB_EVENT_PATH` when available.
  - Validate PR author is `dependabot[bot]` and PR is not draft.
  - Enforce label requirement if configured.
  - If `dry_run`, emit logs and exit 0 without API calls.
  - Otherwise, call GitHub GraphQL API to enable auto-merge and report status.

Dependencies (script-level `uv` block):

- `cyclopts>=3.24` (already in repo) for CLI.
- `httpx>=0.28` (already in repo) for HTTP API calls.

## Revision note

Initial plan created on 2026-01-12. No revisions yet.

Revision 2026-01-12: Marked plan in progress, noted approval, and added the
workflow repository checkout decision before implementation begins.

Revision 2026-01-12: Recorded helper script completion and unit test progress.

Revision 2026-01-12: Updated the helper script location to `workflow_scripts/`
so it does not shadow existing action scripts.

Revision 2026-01-12: Added the reusable workflow, test workflow, and act-based
integration test coverage.

Revision 2026-01-12: Updated repository documentation for the reusable
workflow.

Revision 2026-01-12: Added workflow_scripts tests to pytest discovery so helper
unit tests run by default.

Revision 2026-01-12: Fixed dry-run validation ordering and worktree repo-root
handling; captured gate runs and decisions.

Revision 2026-01-12: Marked plan complete and summarized outcomes.

Revision 2026-01-12: Added act-only uv installation and cache redirection
notes based on workflow test failures.
