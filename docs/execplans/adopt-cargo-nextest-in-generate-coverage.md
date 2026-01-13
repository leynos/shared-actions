# Adopt cargo-nextest for generate-coverage

This Execution Plan (ExecPlan) is a living document. The sections `Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: COMPLETE

PLANS.md was not found in this repository when this plan was created.

## Purpose / Big Picture

The generate-coverage GitHub Action should be able to run Rust coverage with `cargo nextest` by default, while allowing callers to opt out. After this change, running the action with default inputs should install a pinned, verified `cargo-nextest` binary, create a temporary `nextest.toml` when one is missing, and run `cargo llvm-cov nextest` instead of `cargo llvm-cov`. Success is observable by running the action’s tests and seeing coverage jobs use nextest without requiring repository-level nextest configuration.

## Constraints

- Follow repository policy in `AGENTS.md`, including using Makefile targets and running `make check-fmt`, `make typecheck`, `make lint`, and `make test` before each commit that touches GitHub Action logic.
- Use `tee` to capture long command output into temporary log files for later review.
- Preserve the existing public interface of the action, except for the new input `use-cargo-nextest` with default `true`.
- Keep changes scoped to the generate-coverage action and its tests/docs unless a supporting change is required.
- Do not loosen security posture: keep third-party action pins and validate downloaded binaries with a known SHA-256.
- Avoid non-deterministic behaviour (no network-dependent version selection at runtime without a pinned version and hash).

## Tolerances (Exception Triggers)

- Scope: if implementation requires changes to more than 8 files or more than 300 net new lines of code, stop and escalate.
- Interface: if any existing action inputs/outputs must change or be removed, stop and escalate.
- Dependencies: if a new external dependency is required beyond `cargo-nextest` itself or `cargo-binstall`, stop and escalate.
- Iterations: if tests still fail after 2 full test cycles, stop and escalate with logs.
- Ambiguity: if the correct nextest defaults or install strategy are unclear, stop and present options with trade-offs.

## Risks

    - Risk: `cargo binstall` behaviour differs across operating system (OS) runners (path, install location, or hash verification flags).
      Severity: medium
      Likelihood: medium
      Mitigation: verify current install approach in existing action code; add tests that assert the expected command line and hash validation on all supported OS paths.

    - Risk: Creating a temporary `nextest.toml` could override a repository’s intended configuration or conflict with existing config detection.
      Severity: medium
      Likelihood: low
      Mitigation: only create the file when `.config/nextest.toml` is absent; ensure the temp file is isolated and removed after use.

    - Risk: `cargo llvm-cov nextest` requires additional environment or tooling vs `cargo llvm-cov` and may fail on some runners.
      Severity: medium
      Likelihood: medium
      Mitigation: update behavioural tests to cover both paths and validate existing environments; document fallback behaviour when `use-cargo-nextest=false`.

## Progress

    - [x] (2026-01-12 00:00Z) Drafted initial ExecPlan.
    - [x] (2026-01-12 00:20Z) Located generate-coverage action implementation and tests.
    - [x] (2026-01-12 00:45Z) Defined and documented the new input and defaults.
    - [x] (2026-01-12 01:30Z) Implemented nextest install/config/run behaviour with cleanup.
    - [x] (2026-01-12 01:45Z) Added unit/behavioural tests and updated docs.
    - [x] (2026-01-12 02:30Z) Run required Makefile gateways and commit.

## Surprises & Discoveries

    - Observation: None yet.
      Evidence: N/A
      Impact: None.

## Decision Log

    - Decision: Use `cargo-binstall` to install `cargo-nextest` with a pinned version and SHA-256, then run `cargo llvm-cov nextest`.
      Rationale: Satisfies the requirement to use binstall and provides deterministic, verifiable installs.
      Date/Author: 2026-01-12 (Codex)
    - Decision: Pin cargo-nextest to 0.9.120 with platform-specific binary SHA-256 values for Linux, macOS (universal), and Windows (x86_64/aarch64).
      Rationale: Ensures deterministic installs and checksum validation across supported runners.
      Date/Author: 2026-01-12 (Codex)

## Outcomes & Retrospective

Delivered cargo-nextest support for the generate-coverage action with a new
`use-cargo-nextest` input (default true), nextest installation and checksum
verification, and temporary config handling when none is present. Added unit
and behavioural tests covering nextest command selection, config creation, and
install verification. All required Makefile gateways completed successfully.

## Context and Orientation

The generate-coverage action lives under `.github/actions/generate-coverage/` and should contain `action.yml`, `README.md`, `src/`, `tests/`, and `CHANGELOG.md`. The action likely invokes Python scripts in the repo root (for example, modules like `actions_common.py`, `cargo_utils.py`, or `cmd_utils.py`). This plan treats the action as a GitHub Action that may call Python helpers to assemble commands. A “nextest config” refers to `.config/nextest.toml`, a configuration file for `cargo nextest` that controls timeouts and execution behaviour. The new input allows callers to opt out of nextest and continue using the existing `cargo llvm-cov` path.

## Plan of Work

Stage A: Understand current generate-coverage behaviour. Find the action folder, inspect `action.yml`, README, and implementation scripts to see how Rust coverage is executed today. Identify where inputs are parsed and where `cargo llvm-cov` is invoked. Confirm existing test structure and how behavioural tests mock or assert commands. Do not modify code yet.

Stage B: Define the new input and tests. Add `use-cargo-nextest` to `action.yml` with default `true`, document it in the action README input table, and update any config schemas. Add unit tests for input parsing and behaviour switches. Add behavioural tests that assert the correct command paths and that temp nextest config creation is conditional. Ensure tests fail before implementation and pass after.

Stage C: Implement nextest support. Add logic to install `cargo-nextest` when `use-cargo-nextest=true`, using `cargo binstall` and validating against a pinned SHA-256. Add a helper to detect `.config/nextest.toml` and, when missing, create a temporary config with the provided defaults; ensure the file is removed after use. Switch Rust coverage execution to `cargo llvm-cov nextest` when enabled. Preserve the existing `cargo llvm-cov` path when disabled.

Stage D: Hardening and docs. Update CHANGELOG for the action, ensure root README table is updated if required by repo standards, and verify that documentation clearly explains the new input and defaults. Run the required Makefile gateways with logs captured via `tee`. Commit changes after tests pass.

Each stage ends with validation using the project’s Makefile targets and, where applicable, specific tests for the action.

## Concrete Steps

1. Identify current implementation and tests.

   - Run `rg --files -g '.github/actions/generate-coverage/**'` to confirm location.
   - Open `.github/actions/generate-coverage/action.yml`, `.github/actions/generate-coverage/README.md`, and the scripts in `.github/actions/generate-coverage/src/`.
   - Locate test files under `.github/actions/generate-coverage/tests/` and any shared test utilities.

2. Add the new input to metadata and docs.

   - Update `action.yml` with `use-cargo-nextest` defaulting to `true`.
   - Update the README input table and usage examples.
   - If there is a root README table of actions, update it only if required by repo standards.

3. Add tests first (unit and behavioural).

   - Add or update tests that cover:
     - default `use-cargo-nextest=true` path
     - explicit `use-cargo-nextest=false` path
     - absence of `.config/nextest.toml` creating temp file
     - presence of `.config/nextest.toml` skipping temp file creation
     - install command for `cargo-nextest` with pinned version and hash
   - Ensure tests fail before the code changes (document in test logs).

4. Implement logic.

   - Add helper(s) to:
     - detect `.config/nextest.toml`
     - create a temporary nextest config with the required defaults
     - clean up the temporary config after the run
   - Add install step for `cargo-nextest` using `cargo binstall` with pinned version and SHA-256.
   - Switch coverage command to `cargo llvm-cov nextest` when `use-cargo-nextest=true`.
   - Preserve the existing `cargo llvm-cov` behaviour for `use-cargo-nextest=false`.

5. Update changelog and docs.

   - Update `.github/actions/generate-coverage/CHANGELOG.md` with a new entry describing the new input and nextest support.
   - Update any action-specific docs or examples to show the new input.

6. Run validation and commit.

   - From the repo root, run:

        make check-fmt 2>&1 | tee /tmp/shared-actions-check-fmt.log
        make typecheck 2>&1 | tee /tmp/shared-actions-typecheck.log
        make lint 2>&1 | tee /tmp/shared-actions-lint.log
        make test 2>&1 | tee /tmp/shared-actions-test.log

   - Inspect the log files for failures and include relevant excerpts in the plan’s Artifacts section if issues arise.
   - Commit after all gates pass.

## Validation and Acceptance

Acceptance is met when the following are true:

- Running the action’s tests shows that `use-cargo-nextest=true` triggers `cargo llvm-cov nextest`, and `use-cargo-nextest=false` triggers `cargo llvm-cov` without nextest installation.
- The action installs `cargo-nextest` with a pinned version and SHA-256 verification when enabled.
- If `.config/nextest.toml` is missing, the action creates a temporary config with the specified defaults and removes it after use; if present, it is left untouched.
- `make check-fmt`, `make typecheck`, `make lint`, and `make test` succeed.

Quality criteria:

- Tests: all required Makefile gates pass, and new tests cover the behaviour switch.
- Lint/typecheck: no new warnings or failures.
- Documentation: README and CHANGELOG reflect the new input and behaviour.

## Idempotence and Recovery

All steps should be re-runnable. If tests fail, fix the issue and re-run the same Makefile targets. If a temporary `nextest.toml` is created during tests, ensure cleanup logic removes it; remove any leftover temp files before re-running tests. Avoid deleting user-owned files in `.config/`.

## Artifacts and Notes

Expected snippets (example only):

    Installing cargo-nextest vX.Y.Z via cargo binstall
    Verified SHA-256: <expected-hash>
    Running: cargo llvm-cov nextest

Test logs should be stored in `/tmp/shared-actions-*.log` and referenced when diagnosing failures.

## Interfaces and Dependencies

- Input: `use-cargo-nextest` (string or boolean depending on existing conventions), default `true` in `.github/actions/generate-coverage/action.yml`.
- Installation: `cargo-binstall` to install `cargo-nextest` at a pinned version, with SHA-256 verification using a constant stored in the action’s implementation code.
- Commands: use `cargo llvm-cov nextest` for Rust coverage when enabled; use the existing `cargo llvm-cov` path when disabled.
- Files touched at minimum:
  - `.github/actions/generate-coverage/action.yml`
  - `.github/actions/generate-coverage/README.md`
  - `.github/actions/generate-coverage/src/...` (implementation)
  - `.github/actions/generate-coverage/tests/...`
  - `.github/actions/generate-coverage/CHANGELOG.md`

## Revision note

Updated the document wording to expand "Execution Plan (ExecPlan)", define operating system (OS), and align en-GB spellings with review guidance; no execution steps changed.
