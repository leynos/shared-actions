<!-- markdownlint-disable MD013 MD014 -->

# Add cargo-manifests input fallback to generate-coverage

This Execution Plan (ExecPlan) is a living document. The sections `Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: DRAFT

`PLANS.md` was not found in this repository when this plan was created.

## Purpose / Big Picture

The `generate-coverage` action should support Rust projects whose `Cargo.toml` is not at repository root by accepting an optional `cargo-manifests` input (comma/space/newline-separated candidate paths). After this change, Rust or mixed-language detection will still prefer root `Cargo.toml`, otherwise select the first existing path from `cargo-manifests`, and pass that selected file to `cargo llvm-cov` via `--manifest-path`. If `cargo-manifests` is unset, behaviour remains exactly as it is today.

Success is observable when the action:

- still behaves exactly as today with no `cargo-manifests` input,
- detects Rust/mixed projects through the fallback list when root `Cargo.toml` is absent,
- executes `cargo llvm-cov ... --manifest-path <selected-manifest>` for Rust coverage.

## Constraints

- Follow repository policy in `AGENTS.md`, including Makefile-first execution and required gateways for GitHub Action logic changes: `make check-fmt`, `make typecheck`, `make lint`, `make test`.
- Use `set -o pipefail` and `tee` for long-running gateway commands so failures are not masked and logs can be reviewed.
- Keep backward compatibility: when `cargo-manifests` is unset, outputs and behaviour must match current behaviour.
- Preserve existing action inputs/outputs; only add the new optional input and any internal step output required to wire selected manifest path.
- Keep paths relative in caller-facing docs and examples, matching the requirement.
- Keep scope limited to `.github/actions/generate-coverage/` plus this plan document.

## Tolerances (Exception Triggers)

- Scope: if implementation exceeds 7 files or 260 net LOC, stop and escalate.
- Interface: if preserving exact unset-input behaviour requires changing public outputs/semantics beyond the new input, stop and escalate.
- Dependencies: if any new package is needed, stop and escalate.
- Iterations: if required gateways fail after 2 full fix/retest cycles, stop and escalate with logs.
- Ambiguity: if requirement interpretation changes language detection outcomes for existing users, stop and request clarification.

## Risks

- Risk: fallback list parsing could incorrectly treat malformed separators as paths.
  Severity: medium
  Likelihood: medium
  Mitigation: centralize parsing helper and add table-driven tests for comma/space/newline combinations.

- Risk: adding `--manifest-path` can regress command assembly (including cucumber path).
  Severity: medium
  Likelihood: medium
  Mitigation: update command-argument unit tests for both standard and cucumber flows.

- Risk: detection could classify projects as Rust/mixed unexpectedly when a fallback manifest exists but is not intended.
  Severity: low
  Likelihood: medium
  Mitigation: document precedence clearly (root first, then first existing fallback) and keep opt-in via explicit input.

## Progress

- [x] (2026-02-18 16:20Z) Reviewed current `generate-coverage` action inputs, detection script, Rust runner script, and tests.
- [x] (2026-02-18 16:27Z) Drafted this ExecPlan with staged implementation and validation details.
- [ ] Implement detection fallback and selected-manifest output wiring.
- [ ] Implement Rust runner `--manifest-path` wiring.
- [ ] Update docs/changelog/tests and run all required gateways.

## Surprises & Discoveries

- Observation: current Rust detection only checks root `Cargo.toml` in `.github/actions/generate-coverage/scripts/detect.py`, and `run_rust.py` currently runs without explicit `--manifest-path`.
  Evidence: `get_lang()` uses `Path("Cargo.toml").is_file()`, and `get_cargo_coverage_cmd()` does not add manifest-path args.
  Impact: both detection and runtime command assembly need changes for this feature.

- Observation: no MCP resources/tools for the project memory protocol (`qdrant-find`/`qdrant-store`) are available in this execution environment.
  Evidence: `list_mcp_resources` and `list_mcp_resource_templates` returned empty sets.
  Impact: proceeded without project-memory recall/store.

## Decision Log

- Decision: keep coverage execution scalar (one selected manifest), while allowing `cargo-manifests` to be a list of candidate paths for discovery.
  Rationale: `cargo llvm-cov --workspace` already covers a workspace rooted at the selected manifest. Executing multiple manifests in one action run would duplicate test execution, require multi-report merge semantics, and introduce unclear deduplication/ratchet behaviour.
  Date/Author: 2026-02-18 (Codex)

- Decision: detect step will emit the selected manifest path as an internal step output (for Rust/mixed paths) consumed by `run_rust.py`.
  Rationale: keeps selection logic in one place and avoids diverging manifest resolution rules across scripts.
  Date/Author: 2026-02-18 (Codex)

## Outcomes & Retrospective

Planned, not yet executed. Expected outcome: optional fallback manifest detection with unchanged default behaviour and explicit manifest-path passing in Rust coverage commands.

## Context and Orientation

The relevant implementation lives in `.github/actions/generate-coverage/`:

- `action.yml`: action interface and composite step wiring.
- `scripts/detect.py`: determines `lang` and `fmt` outputs from repository files.
- `scripts/run_rust.py`: builds and executes `cargo llvm-cov` commands.
- `tests/test_detect.py`: unit tests for detection behaviour.
- `tests/test_scripts.py`: unit/integration-style tests for Rust command construction and script execution.
- `README.md` and `CHANGELOG.md`: user-facing docs and release notes.

Current baseline behaviour:

- Root `Cargo.toml` present => Rust (or mixed if root `pyproject.toml` also present).
- No root `Cargo.toml` and root `pyproject.toml` present => Python.
- Neither present => error.
- Rust coverage commands currently do not pass `--manifest-path`.

## Plan of Work

Stage A (no behavioural change): add focused tests that describe desired fallback behaviour.

- Extend `tests/test_detect.py` with cases covering:
  - root `Cargo.toml` precedence over `cargo-manifests` candidates,
  - first-existing selection from comma/space/newline-separated candidates,
  - Python-only/error behaviour unchanged when fallback list is empty or has no existing files,
  - mixed detection when selected manifest exists and root `pyproject.toml` exists.
- Extend `tests/test_scripts.py` cases that assert cargo argv to include `--manifest-path` in both primary and cucumber coverage invocations.

Go/no-go: new tests should fail before implementation and pass after implementation.

Stage B: implement manifest selection in detection and export selected path.

- In `scripts/detect.py`:
  - add option/env binding for `INPUT_CARGO_MANIFESTS`.
  - add parser for comma/space/newline-separated tokens.
  - add resolver: root `Cargo.toml` first; else first existing candidate; else none.
  - keep existing Python/error behaviour when no manifest selected.
  - append selected manifest to detect outputs (internal output such as `cargo_manifest=<path>`).
- In `action.yml`:
  - add new input `cargo-manifests` (optional) with clear description.
  - pass `INPUT_CARGO_MANIFESTS` into detect step.
  - pass detect output manifest path to Rust step environment (e.g. `DETECTED_CARGO_MANIFEST`).

Go/no-go: detect script tests pass and default path (input unset) remains unchanged.

Stage C: wire selected manifest into Rust command builder.

- In `scripts/run_rust.py`:
  - add option/env binding for selected manifest path from detect output.
  - update `get_cargo_coverage_cmd()` to include `--manifest-path <selected-manifest>`.
  - ensure cucumber command path inherits same manifest argument.
  - keep defaults compatible for direct test invocation (no env override).

Go/no-go: rust script tests confirm manifest-path in command args for nextest/non-nextest and cucumber flows.

Stage D: update docs/changelog and run full gateways.

- Update `.github/actions/generate-coverage/README.md`:
  - input table row for `cargo-manifests`.
  - flow/behaviour text describing precedence rules.
  - example showing nested manifest fallback.
- Update `.github/actions/generate-coverage/CHANGELOG.md` with a new release entry.
- Run all required gateways and capture logs via `tee`.

## Concrete Steps

1. Add failing tests for desired behaviour.

   - Edit `.github/actions/generate-coverage/tests/test_detect.py`.
   - Edit `.github/actions/generate-coverage/tests/test_scripts.py`.
   - Run targeted tests first:

        uv run --with pytest pytest .github/actions/generate-coverage/tests/test_detect.py -v
        uv run --with pytest pytest .github/actions/generate-coverage/tests/test_scripts.py -v

2. Implement detection and wiring.

   - Edit `.github/actions/generate-coverage/scripts/detect.py`.
   - Edit `.github/actions/generate-coverage/action.yml`.

3. Implement Rust command changes.

   - Edit `.github/actions/generate-coverage/scripts/run_rust.py`.

4. Update docs.

   - Edit `.github/actions/generate-coverage/README.md`.
   - Edit `.github/actions/generate-coverage/CHANGELOG.md`.

5. Run required gateways from repository root (with logs).

        set -o pipefail; make check-fmt 2>&1 | tee /tmp/shared-actions-check-fmt.log
        set -o pipefail; make typecheck 2>&1 | tee /tmp/shared-actions-typecheck.log
        set -o pipefail; make lint 2>&1 | tee /tmp/shared-actions-lint.log
        set -o pipefail; make test 2>&1 | tee /tmp/shared-actions-test.log

6. If any gateway fails, fix and rerun the failed gateway(s) until green, then rerun full sequence if failures touched shared logic.

## Validation and Acceptance

Acceptance criteria:

- With no `cargo-manifests` input, detection and runtime behaviour match current behaviour.
- With root `Cargo.toml` present, that manifest is always selected regardless of `cargo-manifests` values.
- With no root `Cargo.toml`, first existing path from `cargo-manifests` is selected.
- If no manifest exists after fallback, action keeps current Python-only/error behaviour.
- Rust command invocation includes `--manifest-path <selected-manifest>`.
- `make check-fmt`, `make typecheck`, `make lint`, `make test` all pass.

Observable checks:

- Tests assert detect outputs and cargo argv contents.
- Command logs show `cargo llvm-cov ... --manifest-path <path> ...`.

## Idempotence and Recovery

The edits are text-only and re-runnable. If a test run partially writes coverage outputs in temp paths, remove only test temp artefacts and rerun. If command-order assertions fail due argument position changes, adjust tests to assert stable semantic invariants (presence and value pairing of `--manifest-path`) rather than brittle absolute positions where appropriate.

## Artifacts and Notes

Expected command snippet after implementation:

    $ cargo llvm-cov nextest --manifest-path rust-toy-app/Cargo.toml --workspace --summary-only --lcov --output-path <...>

Expected detect output snippet for fallback case:

    lang=rust
    fmt=lcov
    cargo_manifest=rust-toy-app/Cargo.toml

## Interfaces and Dependencies

- New action input:
  - `cargo-manifests` (optional string): comma/space/newline-separated relative candidate paths.
- Internal step contract:
  - detect step emits selected manifest path output (for Rust/mixed detection paths).
  - rust step consumes selected manifest path via environment variable.
- No new external dependencies.
- Files expected to change:
  - `.github/actions/generate-coverage/action.yml`
  - `.github/actions/generate-coverage/scripts/detect.py`
  - `.github/actions/generate-coverage/scripts/run_rust.py`
  - `.github/actions/generate-coverage/tests/test_detect.py`
  - `.github/actions/generate-coverage/tests/test_scripts.py`
  - `.github/actions/generate-coverage/README.md`
  - `.github/actions/generate-coverage/CHANGELOG.md`

## Revision note

Initial draft created to plan `cargo-manifests` fallback detection and manifest-path wiring for Rust coverage, with a deliberate scalar execution decision and explicit backward-compatibility checks.
