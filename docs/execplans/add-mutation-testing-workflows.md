# Mutation-testing reusable workflows (plan)

This ExecPlan is a living document. The sections `Constraints`, `Tolerances`,
`Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: DRAFT

PLANS.md: none found in this repo.

## Purpose / Big Picture

Create two reusable workflows that bring scheduled, informational mutation
testing to the leynos/\* estate:

- `mutation-cargo.yml` — Rust repositories, using
  [`cargo-mutants`](https://mutants.rs/).
- `mutation-mutmut.yml` — Python repositories, using
  [`mutmut`](https://mutmut.readthedocs.io/).

Both generalize the workflow proven in `leynos/wireframe`
(`.github/workflows/mutation-testing.yml`, ADR-007 there): a daily cron with
a change-detection guard (cheap no-op on quiet days), mutation runs scoped
to recently changed files, survivors reported via the job summary and
artefacts, and no gating of pull requests. Success looks like a repository
adding a thin caller workflow pinned to a shared-actions commit SHA and
receiving mutation reports with no further per-repo logic. The estate
rollout itself is tracked separately in
`~/docs/mutation-testing-rollout-plan.md` (operator machine); this plan
covers only the shared implementation.

To observe success: run the workflow integration tests locally
(`ACT_WORKFLOW_TESTS=1 make test`), then add a caller to a pilot repository
(wireframe for Rust; cmd-mox or falcon-pachinko for Python), dispatch it
manually, and check the job summary for the survivors table and the
uploaded `mutation-report-*` artefacts.

## Constraints

- Follow `docs/scripting-standards.md` for all new scripts: Python ≥3.13,
  self-contained `uv` script blocks, Cyclopts with `Env("INPUT_", ...)`
  configuration, `plumbum` for external processes (no `subprocess`),
  `pathlib` for paths, idempotent behaviour, and UNIX exit codes.
- Non-trivial shell logic from the wireframe prototype (change detection,
  exit-code policy, summary generation) must be ported to tested Python
  helper scripts; workflow `run:` blocks stay thin.
- Helper scripts live in `workflow_scripts/` with tests in
  `workflow_scripts/tests/` (Decision Log precedent from the
  dependabot-automerge plan: avoids module collisions with per-action
  `scripts/` packages during `make test`).
- Follow `docs/local-validation-of-github-actions-with-act-and-pytest.md`
  for workflow integration tests: black-box `act` runs driven from
  `tests/workflows/test_*.py`, asserting on artefacts, workspace side
  effects, and `act --json` logs, using the shared `conftest.py` probe and
  markers.
- Pin all third-party actions by full commit SHA. Pin the default
  `cargo-mutants` and `mutmut` versions in the reusable workflows (not in
  callers): both tools' report formats are unstable, so the parsing logic
  and the tool version must travel together.
- Least-privilege permissions: the jobs need `contents: read` only.
  Document required caller permissions explicitly.
- Workflow inputs must reach scripts as `INPUT_*` environment variables via
  `env:` blocks; never interpolate `${{ ... }}` expressions into script
  text (shell-injection vector — confirmed relevant in the wireframe
  implementation, where file names reach the command line).
- Each workflow gets a caller-facing documentation page mirroring
  `docs/dependabot-automerge-workflow.md` (behaviour, permissions, usage
  example, inputs table, local validation).
- Commit gates per `AGENTS.md`: `make check-fmt`, `make typecheck`,
  `make lint`, and `make test` must pass before review. New script
  directories must be added to the `typecheck` search-path list.
- en-GB-oxendict spelling throughout documentation.

## Tolerances (Exception Triggers)

- Scope: if implementation requires changes to more than 14 files or more
  than 1,200 net new lines, stop and escalate.
- Dependencies: `mutmut` and `cargo-mutants` are runtime-only tool
  installations inside workflow jobs (via `uv run --with mutmut` /
  `cargo binstall`); they must not be added to `pyproject.toml`. If any
  other new `pyproject.toml` dependency is required, stop and escalate.
- Interface: no changes to existing actions or workflows other than
  Makefile `typecheck` wiring. If an existing interface must change, stop
  and escalate.
- Iterations: if the `act` integration tests still fail after 3 fix
  attempts, stop and escalate with the failing logs.
- Ambiguity: if mutmut 3.x proves unable to support the informative
  exit-code contract or per-file scoping in any usable form (Stage A
  research), stop and present options before building Stage C.

## Risks

- Risk: mutmut's CLI, configuration, and results format differ
  significantly from cargo-mutants (no direct `--file`/`--in-diff`
  equivalents in some versions; results stored in a local cache/dir).
  Severity: medium. Likelihood: medium. Mitigation: Stage A research spike
  against the pinned mutmut version before designing the workflow inputs;
  the two workflows share reporting conventions, not implementation.
- Risk: `act` cannot faithfully execute long cargo/mutation runs, making
  integration tests slow or flaky. Severity: medium. Likelihood: medium.
  Mitigation: integration tests exercise the guard, scoping, exit-code
  policy, and summary paths with stubbed tool binaries (cmd-mox-style
  fakes on `PATH` writing synthetic `outcomes.json` / mutmut results);
  real mutation runs are validated on the pilot repositories, not under
  `act`.
- Risk: `outcomes.json` schema drift in future cargo-mutants releases
  breaks the summary parser. Severity: low. Likelihood: low. Mitigation:
  version pinned next to the parser; parser unit tests carry a
  representative fixture; bumps update both together.
- Risk: OIDC self-checkout (the dependabot-automerge pattern for locating
  the pinned workflow source) adds complexity the mutation workflows may
  not need. Severity: low. Likelihood: medium. Mitigation: Stage B
  evaluates whether helper scripts can instead be fetched via
  `actions/checkout` of `leynos/shared-actions` at a caller-supplied ref
  input defaulting to the pinned SHA; adopt the simplest mechanism that
  keeps script and workflow versions in lockstep, and record the choice in
  the Decision Log.

## Progress

- [ ] Stage A: mutmut research spike (semantics, exit codes, scoping,
  results format; pin candidate version).
- [ ] Stage B: `mutation-cargo.yml` reusable workflow + helper scripts +
  unit tests.
- [ ] Stage C: `mutation-mutmut.yml` reusable workflow + helper scripts +
  unit tests.
- [ ] Stage D: `act` integration tests for both workflows (guard path,
  dispatch path, summary generation with stubbed tools).
- [ ] Stage E: caller documentation pages and README index updates.
- [ ] Stage F: gates (`check-fmt`, `typecheck`, `lint`, `test`,
  `ACT_WORKFLOW_TESTS=1 make test`), CodeRabbit review, commit.
- [ ] Stage G: pilot caller validation (wireframe migration for Rust; one
  Python repo), then record pinned tool versions confirmed by real runs.

## Surprises & Discoveries

Seeded from the wireframe implementation (2026-07-04); append new items as
work proceeds.

- Observation: `cargo mutants --output DIR` creates `mutants.out/` within
  `DIR` rather than renaming it. Evidence: `OutputDir::new` in upstream
  `src/output.rs`. Impact: rely on default output locations; never pass
  `--output` and expect a flat directory.
- Observation: `cargo mutants` exit codes are informative, not merely
  pass/fail: 0 all caught, 1 usage error, 2 missed mutants, 3 timeouts,
  4 baseline failing, 5/6 `--in-diff` problems, 70 internal error.
  Evidence: upstream handbook. Impact: an informational workflow must
  treat 2 and 3 as success and let 1/4/70 fail the job.
- Observation: scoping that matches no mutants exits 0 with a warning.
  Evidence: upstream handbook. Impact: stale file arguments are benign;
  tip-existence filtering is belt-and-braces.
- Observation: GitHub cron start times drift by up to an hour, and
  scheduled workflows are suspended after 60 days of repository
  inactivity. Impact: the change-detection window must exceed the cadence
  (wireframe uses 25 hours for a daily cron); suspension is an acceptable
  failure mode.
- Observation: `--in-place` (upstream CI recommendation) reuses the build
  cache instead of copying the tree, but is incompatible with `-j`.
  Impact: default to `--in-place`, no parallel jobs.
- Observation: companion/testkit crates whose coverage lives in a sibling
  crate produce predominantly false survivors when mutated against their
  own tests only. Impact: extra-crate handling is opt-in, with the caveat
  documented for callers.

## Decision Log

- 2026-07-04: Reusable workflows, not composite actions. Rationale: the
  jobs need `on: workflow_call`, job-level `permissions`, `timeout-minutes`,
  and artefact handling — the documented threshold for full workflows in
  `docs/composite-actions-vs-full-workflows.md`.
- 2026-07-04: Port the wireframe prototype's bash/jq logic to Python
  helper scripts under `workflow_scripts/`. Rationale: scripting standards
  require it, the logic (change detection, exit-code policy, summary
  rendering) is testable with fixtures, and the jq parsing already proved
  fragile enough to warrant unit tests.
- 2026-07-04: Tool version pins live in the reusable workflows with
  override inputs, not in callers. Rationale: report-format parsing and
  tool version must move together; callers should get working defaults.
- 2026-07-04: Integration tests stub the mutation tools rather than
  running real mutation testing under `act`. Rationale: real runs are
  unbounded in time and dominated by the tools themselves, which are not
  what these tests protect; the workflow logic (guard, scoping, exit
  codes, summary, artefacts) is fully exercisable with synthetic reports.

## Outcomes & Retrospective

(To be completed.)

## Context and Orientation

Key references in this repository:

- `.github/workflows/dependabot-automerge.yml` — the structural template:
  `workflow_call` inputs, SHA-pinned third-party actions, `ACT` bypass for
  local runs, helper invocation via `uv run --script`.
- `docs/dependabot-automerge-workflow.md` — the caller-facing doc shape to
  mirror.
- `docs/scripting-standards.md`, `docs/developers-guide.md` — mandatory
  script conventions.
- `docs/local-validation-of-github-actions-with-act-and-pytest.md` and
  `tests/workflows/` — the integration-test harness
  (`conftest.py` probe, `skip_unless_act` / `skip_unless_workflow_tests`
  markers, `ACT_WORKFLOW_TESTS` Makefile wiring).
- `workflow_scripts/` — home for reusable-workflow helper scripts and
  their tests.
- `Makefile` — `check-fmt`, `typecheck` (explicit search-path list to
  extend), `lint` (ruff + action-validator), `test`.

External references:

- `leynos/wireframe` branch `adopt-cargo-mutants-workflow`:
  `.github/workflows/mutation-testing.yml` (the proven prototype),
  `docs/adr-007-mutation-testing-with-cargo-mutants.md` (decision record
  incl. risks and jq field names), and
  `docs/execplans/adopt-cargo-mutants-workflow.md` (retrospective with
  lessons feeding this plan).
- cargo-mutants handbook: <https://mutants.rs/> (exit codes, output
  directory, `--in-place`, `--shard`, `--in-diff`).
- `~/docs/mutation-testing-rollout-plan.md` (operator machine) — estate
  rollout waves and repo eligibility.

## Plan of Work

### Stage A: mutmut research spike

Determine, against a pinned mutmut 3.x version (use the local `mutmut`
skill and upstream docs):

1. Configuration surface: `[tool.mutmut]` in `pyproject.toml`
   (`paths_to_mutate`, `tests_dir`) and CLI overrides.
2. Exit-code taxonomy: which codes distinguish "survivors found" from
   genuine failure, and whether the informative-outcome policy maps
   cleanly.
3. Scoping: whether per-file mutation is supported (CLI argument,
   config, or unsupported — in which case the guard gates run/no-run
   only, and the workflow mutates `paths_to_mutate` wholesale).
4. Results format: how `mutmut results` (or its JSON/junitxml output)
   enumerates survivors with file/line/description for the summary table.
5. Baseline behaviour: how a failing test suite manifests, so the
   workflow can fail loudly rather than report nonsense.

Record findings in this plan; they parameterize Stage C.

### Stage B: mutation-cargo reusable workflow

1. `workflow_scripts/mutation_detect_changes.py`: given window hours,
   path patterns, and crate-dir mappings (`INPUT_*` env vars), emit
   `has_changes`, per-target file lists, and `dispatch` to
   `$GITHUB_OUTPUT`; write the skip message to `$GITHUB_STEP_SUMMARY`.
   Unit tests cover bucketing, tip-existence filtering, window edges, and
   dispatch bypass (fixture git repos via `test_support`).
2. `workflow_scripts/mutation_run_cargo.py`: wrap the `cargo mutants`
   invocation (scoped `--file` arguments, `--in-place`,
   `--timeout-multiplier`, optional `--dir`), applying the exit-code
   policy (0/2/3 succeed; 1/4/70 and unknowns fail). Unit tests use
   cmd-mox fakes.
3. `workflow_scripts/mutation_summarize_cargo.py`: parse `outcomes.json`
   (fixture-backed) and append counts plus the survivors table to
   `$GITHUB_STEP_SUMMARY`.
4. `.github/workflows/mutation-cargo.yml`: `workflow_call` inputs —
   `paths` (default `src/**/*.rs,examples/**/*.rs,benches/**/*.rs`),
   `extra-crate-dirs` (default empty; wireframe's `wireframe_testing`
   case), `window-hours` (25), `timeout-multiplier` (3),
   `timeout-minutes` (300), `cargo-mutants-version` (pinned default),
   `runs-on` (default `ubuntu-latest`). Single job, `contents: read`,
   artefact uploads per target, `ACT` bypass mirroring
   dependabot-automerge.

### Stage C: mutation-mutmut reusable workflow

Mirror Stage B with mutmut semantics from Stage A: detect script reused
(path patterns default to `src/**/*.py` or `paths_to_mutate`), run
wrapper applying the researched exit-code policy, summary script parsing
mutmut results into the same summary shape. Inputs: `paths`,
`window-hours`, `timeout-minutes`, `mutmut-version` (pinned default),
`runs-on`, plus whatever scoping input Stage A justifies.

### Stage D: act integration tests

`tests/workflows/test_mutation_cargo_workflow.py` and
`test_mutation_mutmut_workflow.py`, with event fixtures for `schedule`
and `workflow_dispatch`. Stub `cargo-mutants`/`mutmut` binaries on `PATH`
that emit synthetic reports; assert the skip path (no changes), the
dispatch path (full run), summary content, and artefact presence via
`act --json` output and the artifact server path.

### Stage E: documentation

`docs/mutation-cargo-workflow.md` and `docs/mutation-mutmut-workflow.md`
mirroring the dependabot-automerge doc: behaviour, permissions, caller
example pinned by SHA, inputs table, informative exit-code contract,
false-survivor caveat for companion crates, local validation
instructions. Update the README workflow index if one exists.

### Stage F: gates and review

`make check-fmt`, `make typecheck` (with new search paths), `make lint`,
`make test`, and `ACT_WORKFLOW_TESTS=1 make test`; then a CodeRabbit
agent review via scrutineer, clearing all concerns before merge.

### Stage G: pilot validation

Migrate wireframe's bespoke workflow to a `mutation-cargo.yml` caller and
dispatch it; add a caller to one Python pilot (cmd-mox or
falcon-pachinko) and dispatch it. Confirm real `outcomes.json` / mutmut
results parse correctly, record the validated tool versions as the
workflow defaults, and update the estate rollout plan tallies.
