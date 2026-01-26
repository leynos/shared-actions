# Implement default UUIDv7 generator

This ExecPlan is a living document. The sections `Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprizes & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: COMPLETE

PLANS.md was not found in this repository when this plan was created.

## Purpose / big picture

Add a default UUIDv7 generator for correlation IDs so callers can obtain RFC 4122 compliant UUIDv7 values as lowercase hex strings, with millisecond precision and uniqueness across calls. Success is observable by running unit tests and BDD scenarios that validate format, version/variant bits, timestamp precision, and uniqueness, and by updating user-facing docs and design notes to describe the new generator.

## Constraints

- Follow repository policy in `AGENTS.md`, including using Makefile targets and running `make check-fmt`, `make typecheck`, `make lint`, and `make test` before requesting review.
- Use `tee` with `set -o pipefail` for long-running commands so full logs are captured.
- Keep Python runtime compatibility with the repo requirement (`>=3.12`). If a stdlib API is only available in 3.13, use a compatible library instead.
- Ensure UUIDv7 output is RFC 4122 compliant and uses millisecond precision.
- Do not introduce secrets or embed environment-specific paths in docs or tests.
- Keep changes limited to correlation ID lifecycle work and supporting docs/tests unless a missing file must be created to satisfy explicit requirements.

## Tolerances (exception triggers)

- Scope: if implementation requires changes to more than 12 files or more than 500 net new lines of code, stop and escalate.
- Interface: if existing public APIs must be renamed or removed, stop and escalate.
- Dependencies: adding one new runtime dependency or one new dev dependency is acceptable; adding more than that requires escalation.
- Tests: if required Makefile gates fail twice after fixes, stop and escalate with logs.
- Ambiguity: if the referenced design docs or roadmap cannot be located and multiple plausible replacements exist, stop and ask for direction.

## Risks

- Risk: The referenced documentation (`docs/roadmap.md`, `docs/falcon-correlation-id-middleware-design.md`, `docs/complexity-antipatterns-and-refactoring-strategies.md`, `docs/users-guide.md`) is missing in the repo.
  Severity: medium
  Likelihood: high
  Mitigation: confirm whether these docs should be created or are located elsewhere; document any created files and decisions.

- Risk: Python 3.12 compatibility conflicts with a stdlib UUIDv7 API that only exists in 3.13.
  Severity: medium
  Likelihood: medium
  Mitigation: prefer a third-party UUIDv7 library (e.g., `uuid-utils`) if stdlib support is unavailable on 3.12.

- Risk: UUIDv7 uniqueness tests could be flaky if they rely on timing assumptions.
  Severity: low
  Likelihood: medium
  Mitigation: keep uniqueness tests deterministic by using sufficiently large sample sizes without timing expectations and only validate format/version/variant.

## Progress

- [x] (2026-01-26 00:00Z) Drafted initial ExecPlan based on the roadmap item provided in the request.
- [x] (2026-01-26 01:10Z) Began implementation; confirmed referenced roadmap/design/user-guide docs are missing and require creation.
- [x] (2026-01-26 01:40Z) Created roadmap/design/users-guide/complexity docs to satisfy references.
- [x] (2026-01-26 01:55Z) Added pytest unit tests and pytest-bdd scenarios for UUIDv7 generation.
- [x] (2026-01-26 02:10Z) Implemented the default UUIDv7 generator with `uuid-utils`.
- [x] (2026-01-26 02:20Z) Updated documentation and marked the roadmap item complete.
- [x] (2026-01-26 02:45Z) Ran required Makefile gates with `tee` logging.

## Surprizes & discoveries

- Observation: `docs/roadmap.md` and the referenced design/user-guide docs were not found in the repository during initial scan.
  Evidence: `rg --files -g 'roadmap.md'` and `rg -n 'falcon-correlation-id-middleware-design'` returned no matches.
  Impact: The plan includes a discovery step to locate or create these documents before implementation.

- Observation: `pytest.ini` limits `testpaths` to `.github/actions` and `workflow_scripts/tests`.
  Evidence: `pytest.ini` lists only those two directories under `testpaths`.
  Impact: New unit and BDD tests were added under `.github/actions/tests` to ensure they run with `make test`.

## Decision log

- Decision: Defer the exact file/module location for `default_uuid7_generator()` until discovery confirms the intended package layout for correlation ID lifecycle code.
  Rationale: The repo currently contains only top-level Python modules and no correlation ID code; choosing a location prematurely risks misplacement.
  Date/Author: 2026-01-26 (Codex)

- Decision: Create the missing roadmap, design, complexity guidance, and users guide documents referenced by the request.
  Rationale: The referenced documents are absent from the repository; creating them is required to record decisions and update user-facing guidance as specified.
  Date/Author: 2026-01-26 (Codex)

- Decision: Use `uuid-utils` for UUIDv7 generation.
  Rationale: Python 3.12 does not include a stdlib UUIDv7 API, and `uuid-utils` provides RFC 4122 compliant UUIDv7 values with millisecond precision.
  Date/Author: 2026-01-26 (Codex)

- Decision: Place new unit and BDD tests under `.github/actions/tests` to align with pytest discovery.
  Rationale: The default pytest configuration does not collect from `tests/`, so placing tests under `.github/actions/tests` ensures `make test` runs them.
  Date/Author: 2026-01-26 (Codex)

## Outcomes & retrospective

- Delivered `default_uuid7_generator()` returning RFC 4122-compliant UUIDv7 hex strings with millisecond precision and ensured uniqueness/format validation via unit + BDD tests.
- Recorded design decisions in `docs/falcon-correlation-id-middleware-design.md` and documented public behaviour in `docs/users-guide.md`.
- Gates passed; `make typecheck` emits a pre-existing unused-ignore warning in `.github/actions/windows-package/scripts/generate_wxs.py` (not introduced by this work).

## Context and orientation

This repository is a monorepo of shared GitHub Actions with helper Python modules at the repo root (for example, `actions_common.py`, `cmd_utils.py`, `cargo_utils.py`) and tests under `tests/`. There is no existing correlation ID module or UUIDv7 generation logic. The request references a roadmap item and design documents that do not currently exist in `docs/` based on an initial scan. The plan therefore starts with a discovery step to locate or create those documents, then introduces a default UUIDv7 generator, along with both unit tests (pytest) and behavioural tests (pytest-bdd), and updates user-facing documentation.

A “UUIDv7 hex string” means the 32-character, lowercase hexadecimal representation of a UUID (no dashes). RFC 4122 compliance requires the version nibble to be 7 and the variant bits to be RFC 4122 (binary 10xx).

## Plan of work

Stage A: Discovery and alignment. Locate `docs/roadmap.md` (or the intended roadmap file if it was renamed). Locate `docs/falcon-correlation-id-middleware-design.md`, `docs/complexity-antipatterns-and-refactoring-strategies.md`, and `docs/users-guide.md`. If any are missing, decide whether to create them or obtain their correct locations. Record this decision in the design doc. Identify the most appropriate module path for the correlation ID lifecycle code, based on existing package conventions.

Stage B: Testing scaffolding. Add pytest-bdd to the dev dependency group if it is not present. Create unit tests for the generator in `tests/` and BDD feature/step files in a new `tests/bdd/` (or another conventionally named directory if one already exists). Ensure tests fail before implementation by asserting behaviour of a missing or placeholder function.

Stage C: Implementation. Add a `default_uuid7_generator()` function returning a lowercase hex string. Use stdlib UUIDv7 if available on Python 3.12+; otherwise use a third-party library such as `uuid-utils`. Ensure the implementation produces RFC 4122 compliant UUIDv7 values with millisecond precision. Add any helper validation functions if needed for tests or internal checks.

Stage D: Documentation and roadmap. Update the design document with any decisions (library choice, output format, precision notes). Update `docs/users-guide.md` (or create it if missing) to describe the new generator and any new public API. Update the roadmap and check off item 2.2.1 once implementation and tests are complete.

Stage E: Validation. Run the required Makefile gates with `tee` logging. Confirm unit and BDD tests pass and that docs reflect the new behaviour.

Each stage ends with validation. Do not proceed to the next stage if the current stage’s validation fails.

## Concrete steps

1. Discover the referenced docs and intended code location.

   - Run `rg --files -g 'roadmap.md'` and `rg --files -g 'falcon-correlation-id-middleware-design.md'` to find the referenced documents.
   - If missing, search for similarly named docs (e.g., `rg -n "correlation id" docs`).
   - Decide whether to create missing docs or confirm their correct location. Record the decision in the design doc.
   - Use `rg -n "correlation" -S .` to confirm there is no existing correlation ID code and to choose a module location for the new generator.

2. Add dependency scaffolding for testing.

   - If not already present, add `pytest-bdd` to `[dependency-groups].dev` in `pyproject.toml`.
   - If a UUIDv7 library is required (e.g., `uuid-utils`), add it to `[project].dependencies` with an appropriate version range.

3. Write tests first.

   - Add unit tests in `tests/test_correlation_id.py` (or a similarly named file) to validate:
     - output is a 32-character lowercase hex string
     - RFC 4122 version nibble is `7`
     - RFC 4122 variant bits are correct
     - multiple calls produce unique values (use a reasonable sample size, e.g., 1,000)
   - Add a BDD feature file under `tests/bdd/` describing scenarios like “Generate a default UUIDv7 correlation ID” and step definitions that call the generator and assert format/uniqueness.

4. Implement the generator.

   - Create or update the chosen module (for example, a new `correlation_id.py` at the repo root) with:
     - `def default_uuid7_generator() -> str:` returning a lowercase hex string.
     - A single source of truth for UUID creation (stdlib or library).
   - Ensure the generated UUIDs are RFC 4122 compliant and use millisecond precision. If the library exposes timestamp decoding, add minimal validation to tests rather than runtime code.

5. Update documentation and roadmap.

   - Update `docs/falcon-correlation-id-middleware-design.md` with design decisions (library choice, output format, precision, compatibility).
   - Update `docs/users-guide.md` with the new API behaviour and usage guidance.
   - Update `docs/roadmap.md` to check off item 2.2.1 once implementation and tests are complete.

6. Run validation gates.

   - From the repo root, run:

     set -o pipefail
     make check-fmt 2>&1 | tee /tmp/shared-actions-check-fmt.log
     make typecheck 2>&1 | tee /tmp/shared-actions-typecheck.log
     make lint 2>&1 | tee /tmp/shared-actions-lint.log
     make test 2>&1 | tee /tmp/shared-actions-test.log

   - Inspect the log files for failures and keep notes in the plan if any issues arise.

## Validation and acceptance

Acceptance is met when:

- Unit tests validate the UUIDv7 generator output format, version/variant bits, and uniqueness.
- BDD scenarios pass and document the expected behaviour for default UUIDv7 generation.
- The design document captures the library choice and rationale, and the users guide documents the new API.
- The roadmap shows item 2.2.1 checked off.
- `make check-fmt`, `make typecheck`, `make lint`, and `make test` all succeed.

Quality criteria:

- Tests: pytest and pytest-bdd scenarios pass with deterministic assertions.
- Lint/typecheck: no new warnings or failures.
- Docs: design/user guide/roadmap updates are clear and consistent with the implemented API.

## Idempotence and recovery

All steps should be re-runnable. If tests fail, fix the issue and re-run the same Makefile targets. If a new dependency addition causes install failures, revert the dependency change and switch to the alternative UUIDv7 source before retrying. Avoid deleting or overwriting unrelated docs; if a referenced doc is missing, create a new file rather than renaming other docs without approval.

## Artifacts and notes

Expected evidence examples (to update during execution):

    - Unit test output showing new test module passing.
    - BDD scenario output for UUIDv7 generation.
    - Log files: /tmp/make-check-fmt.log, /tmp/make-typecheck.log, /tmp/make-lint.log, /tmp/make-test.log

## Interfaces and dependencies

- New function: `default_uuid7_generator() -> str` returning a lowercase hex UUIDv7 string without dashes.
- Preferred UUIDv7 sources:
  - Use Python stdlib `uuid` if it provides UUIDv7 on the supported runtime.
  - Otherwise use `uuid-utils` (or another UUIDv7-capable library) as the sole new runtime dependency.
- Tests:
  - Unit tests in `tests/` using pytest.
  - Behavioural tests using pytest-bdd with `.feature` files and step definitions under `tests/bdd/`.

## Revision note

Initial draft created to cover roadmap item 2.2.1 for a default UUIDv7 generator, with explicit steps for discovery, tests, implementation, documentation, and validation.

Updated status to COMPLETE after running Makefile validation gates and capturing logs; noted pre-existing typecheck warning and documented outcomes.

Recorded the pytest discovery constraint and documented the decision to place new tests under `.github/actions/tests` so `make test` exercises them.
