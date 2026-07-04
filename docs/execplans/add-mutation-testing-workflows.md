# Mutation-testing reusable workflows (plan)

This ExecPlan is a living document. The sections `Constraints`, `Tolerances`,
`Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: IMPLEMENTED (Stages A–F complete; Stage G pilot validation
outstanding — it requires merging this branch so callers can pin a SHA)

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

- [x] Stage A: mutmut research spike (2026-07-04: empirical against
  mutmut 3.6.0 in a sandbox — see Surprises & Discoveries for exit
  codes, `source_paths` rename, module-glob scoping, and `.meta`
  results layout; candidate pin: mutmut 3.6.0).
- [x] Stage B: `mutation-cargo.yml` reusable workflow + helper scripts +
  unit tests (2026-07-04: `mutation_detect_changes.py`,
  `mutation_run_cargo.py`, `mutation_summarize_cargo.py` under
  `workflow_scripts/` with 36 unit tests; three-job workflow — detect →
  sharded mutants matrix → summarize — reusing the dependabot-automerge
  OIDC source-resolution and `ACT` bypass pattern per job; default
  cargo-mutants pin 27.1.0, current stable at implementation time).
- [x] Stage C: `mutation-mutmut.yml` reusable workflow + helper scripts +
  unit tests (2026-07-04: single-job workflow with an inline detect step
  reusing `mutation_detect_changes.py` with `*.py` pathspec;
  `mutation_run_mutmut.py` combines run, results parsing, and summary —
  no cross-job merge is needed because mutmut has no shard equivalent.
  Changed files translate to module globs per the Stage A finding.
  11 unit tests, faking `uv` on PATH).
- [x] Stage D: `act` integration tests (2026-07-04: wrapper workflows
  `test-mutation-cargo.yml` / `test-mutation-mutmut.yml` with schedule
  and dispatch triggers; `tests/workflows/test_mutation_workflows.py`
  exercises the guard skip path end-to-end under act. See the Decision
  Log for why the mutation-run path is not act-tested).
- [x] Stage E: caller documentation pages and README index updates
  (2026-07-04: `docs/mutation-cargo-workflow.md` and
  `docs/mutation-mutmut-workflow.md` mirroring the dependabot-automerge
  doc shape — behaviour, permissions, usage, inputs, notes, local
  validation — plus README reusable-workflows table entries).
- [x] Stage F: gates and review (2026-07-04: `check-fmt`, `typecheck`,
  `lint`, `test` (989 passed, 14 skipped), `markdownlint`, `nixie` all
  green via scrutineer; CodeRabbit agent review completed with zero
  findings. The act integration tests could not run locally — `act` is
  not installed on this machine — so they remain unverified until run
  in CI or on a machine with act; recorded as an environmental
  limitation, not a code defect).
- [ ] Stage G: pilot caller validation (wireframe migration for Rust; one
  Python repo — cmd-mox suggested, as it is uv-managed), then record
  pinned tool versions confirmed by real runs.

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
- Observation (2026-07-04, first full wireframe dispatch run): 1,821
  mutants found; unmutated baseline 84s build + 120s test;
  `--timeout-multiplier 3` auto-set the per-mutant timeout to 361s;
  typical mutant cost ~16s build + ~12s test (≈28s), with timeout
  mutants costing the full 361s. Total full-run cost ≈14–15 hours on a
  single `ubuntu-latest` runner — far beyond any single-job
  `timeout-minutes` ceiling (max 360). Impact: full (unscoped) runs must
  shard; a single job only suffices for scoped daily runs. `--in-place`
  cache reuse confirmed working (mutant builds ~16s vs 84s cold), and
  `RUSTFLAGS: -D warnings` from setup-rust did not produce spurious
  unviable mutants.
- Observation (2026-07-04, Stage A spike, mutmut 3.6.0, empirical in a
  sandbox project): `[tool.mutmut]` uses `source_paths` (array) —
  `paths_to_mutate` is deprecated with a warning. `mutmut run` exits 0
  both when mutants survive and when functions have no covering tests;
  it exits 1 on a failing baseline ("failed to collect stats. runner
  returned 1") and on invalid filter arguments. So no exit-code masking
  is needed (the informational contract holds by default), and baseline
  failures fail the job naturally — but survivor detection must parse
  results rather than exit codes. Results live in `mutants/` (a project
  copy) as per-source `<path>.meta` JSON files whose `exit_code_by_key`
  maps mutant key → runner exit code (0 = survived, non-zero = killed),
  plus `mutmut-stats.json` (test-selection stats only).
  `mutmut results --all true` prints parseable `name: status` lines
  (statuses observed: killed, survived, no tests, not checked; the
  progress legend also shows timeout, suspicious, skipped). Impact: the
  mutmut summary script parses `mutmut results --all true` output.
- Observation (same spike): file-path scoping (`mutmut run src/x.py`)
  fails with exit 1 in mutmut 3.6 — positional arguments are MUTANT
  NAME globs in module-path form (e.g. `mypkg.calc.x_add*`;
  `mypkg.calc.*` scopes a module). Impact: changed-file scoping
  requires translating file paths to module globs (strip source root,
  s|/|.|, drop `.py`), implemented in the run script. There is no
  `--no-progress` in 3.6; `--max-children` controls parallelism.
- Observation (same run): survivor output mixes genuine assertion gaps
  (preamble arithmetic, stream-rewind comparisons, session-registry
  equality) with scaffolding noise (`src/codec/examples.rs`,
  `src/test_helpers.rs`, `src/connection/test_support.rs`). Impact: the
  cargo workflow should support an exclude-globs input mapped to
  `cargo mutants --exclude` so callers can keep example and test-support
  code out of the survivors table.

- Observation (2026-07-04, Windows CI on PR #319): the fake `cargo`/`uv`
  shims are POSIX shell scripts, so Windows PATH lookup fell through to
  the real tools — the real `cargo` failed with exit 101 (`no such
  command: mutants`), and the mutmut failing-baseline test *passed by
  coincidence* because real mutmut exits 1 on Windows ("please use the
  WSL"). Impact: the shim-dependent tests are now skipped on win32
  (matching the `generate-coverage` precedent, "fake uv helper emits
  POSIX sh"); the reusable workflows only run on `ubuntu-latest`, so
  Windows coverage of the run wrappers carries no signal. The
  coincidental pass is a reminder that platform-conditional skips must
  cover *all* shim-dependent tests, not just the failing ones.

- Observation (2026-07-04, CodeScene on PR #319): advisory code-health
  rules flagged `build_arguments` (excess function arguments — seven
  keywords) and `parse_results` (complex conditional). Fixed by
  introducing the `MutantsInvocation` parameter object and extracting
  `_parse_result_line`; all gates re-verified green.

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
- 2026-07-04: Configurable sharding with `shard-count` defaulting to 6,
  applied to full (dispatch) runs only. Rationale: the first full
  wireframe run measured ~14–15 hours of single-job work for 1,821
  mutants, which no `timeout-minutes` value can accommodate (GitHub caps
  jobs at 360 minutes); 6 shards bring a wireframe-scale full run to
  roughly 2.5–3 hours per leg with headroom under the 300-minute
  default. Scoped daily runs stay single-shard because each shard
  re-pays the baseline build-and-test cost for a handful of mutants.
- 2026-07-04: Reuse the dependabot-automerge OIDC source-resolution
  block verbatim in each job of `mutation-cargo.yml` (the plan's Risks
  section had left room for a simpler mechanism). Rationale: the OIDC
  pattern is proven in this repo, keeps helper scripts in lockstep with
  the caller's pinned workflow SHA with no extra inputs, and its `ACT`
  bypass is exactly what the integration tests need; three copies of a
  known-good block beat one novel mechanism. The `setup-rust` step uses
  the repo's own action via the checked-out `workflow-src` path
  (`uses: ./workflow-src/.github/actions/setup-rust`), skipped under
  `act` where stub binaries stand in for the toolchain.
- 2026-07-04: The wireframe finding about feature-gated tests (its
  issue #571) is addressed by the `extra-args` input (e.g.
  `--all-features`) rather than a dedicated features input — cargo-
  mutants accepts arbitrary flags, and one pass-through input covers
  features, `--jobs`, and future needs without interface churn.
- 2026-07-04: `mutation-mutmut.yml` requires the caller to be a
  uv-managed project with `[tool.mutmut]` configured (`source_paths`,
  test selection, runner); mutmut is injected via
  `uv run --with mutmut==<pin>` so the caller's own dependency set is
  used. pip-only projects are out of scope for the first release —
  documented as a caller prerequisite. mutmut runs are never sharded
  (no `--shard` equivalent exists) and never masked (mutmut already
  exits 0 with survivors); a non-zero `mutmut run` means a failing
  baseline or usage error and fails the job.
- 2026-07-04 (revising the earlier stubbed-tools intent): act
  integration tests cover the guard/skip path only. The original plan
  assumed mutation tools could be stubbed under act, but the harness
  offers no way to inject binaries onto a reusable-workflow job's PATH
  inside the container (callers cannot add steps to a `workflow_call`
  job, and `run_act` only forwards environment variables). The skip-path
  tests still execute the real detection script (git scan, output
  wiring, skip summary) and the whole workflow-call plumbing under act;
  wrapper workflows pass never-matching path prefixes so the result is
  deterministic regardless of repository history. The mutation-run and
  summary paths are covered by 47 unit tests over the helper scripts
  and validated for real in Stage G's pilot repositories.

## Outcomes & Retrospective

Stages A–F implemented 2026-07-04 in five commits (plan hardening plus
one commit per stage). Delivered: two reusable workflows
(`mutation-cargo.yml` with detect → sharded-matrix → summarize jobs;
`mutation-mutmut.yml` single-job), four helper scripts under
`workflow_scripts/` with 47 unit tests, act wrapper workflows and
guard-path integration tests, caller docs, and README index entries.
All deterministic gates green; CodeRabbit review zero findings.

What went well:

- The empirical Stage A spike paid off immediately: mutmut 3.6's
  `source_paths` rename, file-path rejection, and exit-0-with-survivors
  behaviour all contradicted the pre-existing skill/document guidance
  and would have shipped broken scoping without the sandbox test.
- Reusing the dependabot-automerge OIDC/`ACT` pattern meant zero novel
  plumbing; all novelty lives in tested Python.

What changed along the way (see Decision Log): act tests narrowed to
the guard path (no way to stub binaries inside `workflow_call` jobs);
`runs-on` input dropped in favour of the house fixed-runner pattern;
mutmut sharding ruled out (no upstream support).

Remaining work (Stage G, post-merge): migrate wireframe to a
`mutation-cargo.yml` caller, add a caller to a uv-managed Python pilot
(cmd-mox), verify real reports parse, and re-pin tool versions if the
pilots surface drift. The act integration tests should also be
confirmed on a machine with `act` installed (they are opt-in via
`ACT_WORKFLOW_TESTS`).

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
   case), `exclude-globs` (default empty; mapped to
   `cargo mutants --exclude` for example/test-support scaffolding),
   `window-hours` (25), `timeout-multiplier` (3), `timeout-minutes`
   (300), `cargo-mutants-version` (pinned default), `shard-count`
   (default 6), `runs-on` (default `ubuntu-latest`). All jobs
   `contents: read`, artefact uploads per target, `ACT` bypass
   mirroring dependabot-automerge.
5. Sharding design (from the measured wireframe full run — a single job
   cannot finish ~1,800 mutants inside GitHub's 360-minute ceiling):
   - A `detect` job computes `has_changes`, the scoped file lists, and
     whether the run is full (dispatch) or scoped (scheduled), then
     emits a shard matrix as a JSON array via `fromJSON`.
   - **Scoped runs use a single shard** regardless of `shard-count`:
     daily changed-file runs test tens of mutants, and each extra shard
     pays the baseline cost (~3.5 minutes) again.
   - **Full runs fan out** to `shard-count` matrix legs, each invoking
     `cargo mutants --shard k/N` with otherwise identical arguments.
     At the wireframe scale, 6 shards bring a full run to roughly
     2.5–3 hours per leg, comfortably inside the default
     `timeout-minutes: 300`.
   - Each leg uploads `mutants.out/` as `mutation-report-<target>-<k>`;
     a final `summarize` job (`if: always()`) downloads all shard
     artefacts, merges the `outcomes` arrays, and renders one combined
     job summary. The merge lives in
     `workflow_scripts/mutation_summarize_cargo.py` alongside the
     single-report path, with unit tests over multi-shard fixtures.

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
