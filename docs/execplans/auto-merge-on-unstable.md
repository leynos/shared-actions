# Enable Dependabot Auto-Merge When Merge State Is UNSTABLE

This execution plan (ExecPlan) is a living document. The sections `Constraints`, `Tolerances`,
`Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: COMPLETE

PLANS.md: none found in this repository.

## Purpose / Big Picture

Adjust the reusable Dependabot auto-merge workflow so it does not hard-skip pull
requests in `UNSTABLE` merge state. The intended user-visible behaviour is that
the workflow enables GitHub auto-merge earlier (while checks are pending or not
yet green), and GitHub then performs the final merge only after branch protection
and required checks are satisfied.

Success is observable in workflow logs: an eligible Dependabot pull request (PR) with
`automerge_merge_state=UNSTABLE` should now emit `automerge_status=enabled`
instead of `automerge_status=skipped` with `merge-state-unstable`.

## Constraints

- Keep public workflow inputs and secrets unchanged in
  `.github/workflows/dependabot-automerge.yml`.
- Keep default merge method and label gating behaviour unchanged.
- Preserve existing skip behaviour for genuinely unsafe states:
  `DIRTY`, `BLOCKED`, `BEHIND`, and `CONFLICTING`.
- Preserve retry semantics for unknown mergeability (`UNKNOWN` states).
- Do not add new Python dependencies or external actions.
- Keep script compatible with Python 3.13 and existing Cyclopts/env-var wiring.
- Keep emitted output keys stable (`automerge_status`, `automerge_reason`,
  `automerge_merge_state`, `automerge_mergeable_state`) so downstream consumers
  and tests continue to work.

If any required change violates these constraints, stop and escalate rather than
silently broadening scope.

## Tolerances (Exception Triggers)

- Scope: if implementation requires changes to more than 5 files or more than
  220 net lines, stop and escalate.
- Interface: if any reusable workflow input/secret name or default must change,
  stop and escalate.
- Dependencies: if any new dependency is required, stop and escalate.
- Validation: if quality gates still fail after 2 full fix attempts, stop and
  escalate with failure logs and hypotheses.
- Ambiguity: if GitHub API behaviour indicates `enablePullRequestAutoMerge`
  cannot be enabled for `UNSTABLE` in this repository configuration, stop and
  escalate with evidence.

## Risks

- Risk: Enabling auto-merge at `UNSTABLE` may reintroduce failure modes that led
  to the stricter gating added in commit `3200763`.
  Severity: medium
  Likelihood: medium
  Mitigation: keep all other merge-state skip gates, add targeted unit coverage,
  and validate logs from a representative test case.

- Risk: Some repositories disable auto-merge or have rules that reject enabling
  auto-merge in specific transient states.
  Severity: medium
  Likelihood: low
  Mitigation: keep existing error propagation and ensure diagnostics still expose
  merge state and reason.

- Risk: Behavioural drift between docs and implementation.
  Severity: low
  Likelihood: medium
  Mitigation: update `docs/dependabot-automerge-workflow.md` alongside logic and
  tests in the same change set.

## Progress

- [x] (2026-02-12 00:00Z) Reviewed current automerge workflow/script behaviour
      and identified `UNSTABLE` hard-skip path.
- [x] (2026-02-12 00:00Z) Drafted ExecPlan in
      `docs/execplans/auto-merge-on-unstable.md`.
- [x] (2026-02-12 00:00Z) Began implementation of planned changes.
- [x] (2026-02-12 00:00Z) Implemented merge-state classification change to allow
      `UNSTABLE`.
- [x] (2026-02-12 00:00Z) Updated unit tests to assert enabling on `UNSTABLE`
      state.
- [x] (2026-02-12 00:00Z) Updated workflow documentation to describe new
      `UNSTABLE` handling.
- [x] (2026-02-12 00:00Z) Ran quality gates (`make check-fmt`,
      `make typecheck`, `make lint`, `make test`) with `tee` logs and
      `pipefail`.
- [x] (2026-02-12 00:00Z) Finalized outcomes and retrospective evidence.

## Surprises & Discoveries

- Discovery: The current repository has no `PLANS.md`, so this ExecPlan is
  governed by the `execplans` skill format and `AGENTS.md` instructions.
- Discovery: Qdrant memory Model Context Protocol (MCP) tools (`qdrant-find`, `qdrant-store`) are not
  exposed in this execution environment; no remote project-memory lookup was
  possible from this session.
- Discovery: The existing unit suite already has a dedicated test asserting
  `UNSTABLE` is skipped (`merge_state_unstable_skips`), which will need to be
  inverted rather than adding entirely new harness plumbing.
- Discovery: `make typecheck` emits an existing non-fatal warning in
  `.github/actions/windows-package/scripts/generate_wxs.py` about an unused
  `type: ignore`; this warning does not fail the gate.

## Decision Log

- Decision: Implement the preferred strategy by allowing `UNSTABLE` in merge
  state classification, instead of adding additional workflow triggers.
  Rationale: this matches user preference and preserves a single-event-path
  architecture while letting GitHub enforce protections at merge time.
  Date/Author: 2026-02-12 (assistant).

- Decision: Keep `UNKNOWN` as retry-only and keep non-mergeable states as skip.
  Rationale: retains conservative handling for ambiguous/unsafe states while
  removing only the single gate requested.
  Date/Author: 2026-02-12 (assistant).

- Decision: Validate primarily via existing Python unit tests and repository
  quality gates, not by expanding workflow trigger matrix in this change.
  Rationale: minimizes blast radius and keeps this change focused on policy
  semantics.
  Date/Author: 2026-02-12 (assistant).

## Context and Orientation

Primary implementation files:

- `workflow_scripts/dependabot_automerge.py`: merge-state classification and
  enable/skip decision logic.
- `workflow_scripts/tests/test_dependabot_automerge.py`: unit coverage for live
  execution scenarios, including the current `UNSTABLE` skip assertion.
- `docs/dependabot-automerge-workflow.md`: reusable workflow behaviour and policy
  documentation.

Relevant pre-change behaviour (captured during planning):

- `MERGE_STATE_SKIP_REASONS` included `MergeStateStatus.UNSTABLE`, producing
  reason `merge-state-unstable`.
- `_classify_merge_state()` returns `skip` for any merge state in that map.
- `_handle_live_execution()` emits a `skipped` decision for any `skip`
  classification.

## Plan of Work

Stage A: Update merge-state policy in script.

- Edit `workflow_scripts/dependabot_automerge.py` to remove `UNSTABLE` from
  `MERGE_STATE_SKIP_REASONS`.
- Keep `DIRTY`, `BLOCKED`, and `BEHIND` in skip reasons and keep
  `CONFLICTING` in mergeable skip reasons.
- Do not alter retry config/env vars; `UNKNOWN` handling remains unchanged.

Stage B: Update and extend automated tests.

- In `workflow_scripts/tests/test_dependabot_automerge.py`, update the
  parameterised live-execution case to test ID `merge_state_unstable_enables`
  with parameters:
  - `should_enable=True`
  - `expected_status="enabled"`
  - `expected_reason="enabled"`
- Confirm existing skip tests (`DIRTY`, `BLOCKED`, `BEHIND`, `CONFLICTING`)
  remain intact to guard against over-broad policy changes.

Stage C: Update docs to match runtime behaviour.

- Update `docs/dependabot-automerge-workflow.md` to explicitly state that an
  eligible Dependabot PR in `UNSTABLE` can still have auto-merge enabled, with
  actual merge deferred by GitHub until checks/protections pass.
- Keep security and permissions guidance unchanged.

Stage D: Validate and capture evidence.

- Run all required quality gates from repo root with `pipefail` and `tee`:

      set -o pipefail; make check-fmt 2>&1 | tee /tmp/check-fmt.log
      set -o pipefail; make typecheck 2>&1 | tee /tmp/typecheck.log
      set -o pipefail; make lint 2>&1 | tee /tmp/lint.log
      set -o pipefail; make test 2>&1 | tee /tmp/test.log

- If any gate fails, inspect the corresponding `/tmp/*.log`, apply a minimal
  corrective fix, and rerun the failed gate(s). If two attempts fail, escalate.

## Validation and Acceptance

Behavioural acceptance criteria:

- For an otherwise eligible Dependabot PR where
  `mergeStateStatus="UNSTABLE"` and `mergeable="MERGEABLE"`, the helper enables
  auto-merge (`automerge_status=enabled`, reason `enabled`).
- For `DIRTY`, `BLOCKED`, `BEHIND`, and `CONFLICTING`, helper still emits
  `automerge_status=skipped` with existing reasons.
- Existing author, draft, and label gates are unaffected.

Execution acceptance criteria:

- `make check-fmt`, `make typecheck`, `make lint`, and `make test` all exit 0.
- Updated unit tests in
  `workflow_scripts/tests/test_dependabot_automerge.py` pass and clearly
  demonstrate the `UNSTABLE -> enabled` policy change.

## Idempotence and Recovery

- The workflow remains idempotent because it already short-circuits with
  `already-enabled` if auto-merge is already active.
- If mutation enablement fails due to repository settings, rerunning after
  settings correction is safe; no destructive operations are performed.
- Rollback path is a single policy reversion: re-add `UNSTABLE` to
  `MERGE_STATE_SKIP_REASONS` and restore the test expectation.

## Outcomes & Retrospective

Implemented exactly the planned `UNSTABLE` policy change with no scope expansion.

Actual files changed:

- `workflow_scripts/dependabot_automerge.py`
- `workflow_scripts/tests/test_dependabot_automerge.py`
- `docs/dependabot-automerge-workflow.md`
- `docs/execplans/auto-merge-on-unstable.md`

Validation evidence:

- `set -o pipefail; make check-fmt 2>&1 | tee /tmp/check-fmt.log` passed.
- `set -o pipefail; make typecheck 2>&1 | tee /tmp/typecheck.log` passed
  (existing warning only).
- `set -o pipefail; make lint 2>&1 | tee /tmp/lint.log` passed.
- `set -o pipefail; make test 2>&1 | tee /tmp/test.log` passed
  (`635 passed, 86 skipped`).

Retrospective:

- The change was low-risk because merge-state behaviour is centralized in
  `MERGE_STATE_SKIP_REASONS`; removing one enum key cleanly changed policy.
- Existing parametrized tests made the behaviour flip explicit with a one-case
  expectation update.
- No deviations from plan were required.
