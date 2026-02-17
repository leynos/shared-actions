# Refactor validate_packages.py into focused modules

This Execution Plan (ExecPlan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`,
and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: COMPLETE


## Purpose / big picture

Improve cohesion and maintainability of the validate-linux-packages action by
extracting related functions from the monolithic validate_packages.py (734
lines) into five focused modules. After this change, developers can navigate
the codebase more easily, understanding where architecture-related logic lives
versus diagnostic formatting versus path validation. The public API remains
unchanged, all tests pass without modification, and imports from
validate_packages continue to work via re-exports.


## Constraints

Hard invariants that must hold throughout implementation:

- Public API stability: all functions currently in `__all__` of
  validate_packages.py must remain importable from validate_packages after the
  refactoring (via re-export).
- Test compatibility: all existing tests must pass without modification. Tests
  import via `validate_packages_module` fixture which loads validate_packages.py
  dynamically.
- Backward compatibility: any code importing from validate_packages (including
  validate_cli.py and tests) must continue working without changes.
- No functional changes: this is a pure refactoring; behaviour must remain
  identical.
- Type safety: all type hints must remain valid; typecheck must pass.
- The duplicate `_trim_output()` function must be resolved (two definitions at
  lines 148 and 385 with different signatures).


## Tolerances (exception triggers)

- Scope: if refactoring requires changes to more than 7 files (5 new modules +
  validate_packages.py + one test discovery), stop and escalate.
- Interface: if any function in `__all__` changes signature or behaviour, stop
  and escalate.
- Dependencies: if new external dependencies are required, stop and escalate.
- Iterations: if tests fail after initial refactoring and fixing imports takes
  more than 3 attempts, stop and escalate.
- Time: if any module extraction takes more than 30 minutes, stop and escalate.


## Risks

- Risk: The duplicate `_trim_output()` functions have different signatures (one
  at line 148 with line_limit and char_limit parameters, one at line 385
  without). The second definition overrides the first, so some call sites
  expecting the multi-line variant may not work correctly.
  Severity: medium
  Likelihood: high
  Mitigation: Audit all call sites and consolidate into a single implementation
  that preserves the intended behaviour for all callers.

- Risk: Tests use dynamic module loading via fixture; imports from new modules
  might not be properly discovered.
  Severity: low
  Likelihood: low
  Mitigation: Verify re-exports work correctly with the fixture-based loading.

- Risk: Circular import dependencies may emerge when extracting modules.
  Severity: medium
  Likelihood: low
  Mitigation: Keep imports unidirectional; validate_packages imports from the
  new modules, not vice versa.


## Progress

- [x] (2025-02-17) Audit `_trim_output()` duplicate and resolve signature conflict.
- [x] (2025-02-17) Create validate_formatters.py with formatting functions.
- [x] (2025-02-17) Create validate_sandbox_diagnostics.py with diagnostic functions.
- [x] (2025-02-17) Create validate_path_checks.py with path validation functions.
- [x] (2025-02-17) Create validate_arch.py with architecture functions.
- [x] (2025-02-17) Create validate_locators.py with package discovery functions.
- [x] (2025-02-17) Update validate_packages.py with imports and re-exports.
- [x] (2025-02-17) Run `make typecheck` and verify no errors (1 pre-existing warning).
- [x] (2025-02-17) Run `make test` and verify all tests pass (635 passed, 86 skipped).
- [x] (2025-02-17) Run `make lint` and verify no errors.
- [x] (2025-02-17) Run `make check-fmt` and verify formatting is correct.


## Surprises & discoveries

- Observation: The duplicate `_trim_output()` functions serve different purposes.
  The first (line 148) preserves multi-line structure with line limits, while the
  second (line 385) normalises to a single line for logging. Both are needed.
  Evidence: Call sites at lines 174, 184, 191, 283, 286 use multi-line variant
  for diagnostics; lines 419, 426 use single-line variant for logging.
  Impact: Resolution was to rename the second function to `_trim_output_single_line()`
  rather than delete it, preserving both behaviours.

- Observation: The `_PATH_CHECK_TIMEOUT_SECONDS` constant is used in
  validate_sandbox_diagnostics.py but logically belongs with path checks.
  Evidence: Used in `_execute_diagnostic_command()` for path diagnostic commands.
  Impact: Duplicated the constant in validate_sandbox_diagnostics.py to avoid
  circular import dependency while keeping path check timeout logic together.


## Decision log

- Decision: Rename second `_trim_output()` to `_trim_output_single_line()` instead
  of consolidating into one function.
  Rationale: The two functions serve distinctly different purposes - multi-line
  diagnostic formatting vs single-line logging. Combining them would require
  complex conditional logic and reduce code clarity. Renaming makes the intent
  explicit and preserves both behaviours cleanly.
  Date/Author: 2025-02-17 / Agent

- Decision: Create modules in dependency order: formatters, diagnostics, path_checks,
  arch, locators.
  Rationale: Avoids circular imports by ensuring each module only imports from
  modules created earlier or from existing validate_* modules. Formatters has no
  dependencies on new modules, so it comes first.
  Date/Author: 2025-02-17 / Agent

- Decision: Move PolytheneSession imports to TYPE_CHECKING blocks in new modules.
  Rationale: Ruff lint rule TC002 requires third-party imports used only for type
  hints to be in TYPE_CHECKING blocks. This reduces runtime import overhead and
  makes type-only dependencies explicit.
  Date/Author: 2025-02-17 / Agent

- Decision: Keep validate_packages.py as orchestration layer only.
  Rationale: Retained `_exec_with_diagnostics()`, `_install_and_verify()`,
  `_validate_package()`, `_MetadataValidators`, and public entry points
  `validate_deb_package()` and `validate_rpm_package()` in validate_packages.py.
  These functions coordinate between multiple modules and belong at the
  orchestration layer.
  Date/Author: 2025-02-17 / Agent


## Outcomes & retrospective

Successfully refactored validate_packages.py from 734 lines to 377 lines by
extracting 5 focused modules. All 635 tests pass, all commit gates pass.

Modules created:
- validate_formatters.py (51 lines): Output trimming and stderr extraction
- validate_arch.py (70 lines): Architecture mapping and platform detection
- validate_locators.py (49 lines): Package discovery (locate_deb, locate_rpm, ensure_subset)
- validate_sandbox_diagnostics.py (176 lines): Diagnostic collection for sandbox troubleshooting
- validate_path_checks.py (151 lines): Path validation with Python fallback logic

What worked well:
- Dependency-ordered module creation prevented circular imports
- Renaming `_trim_output()` variants instead of consolidating preserved clarity
- Re-exporting public functions via `__all__` in validate_packages.py maintained
  backward compatibility - no test changes required
- TYPE_CHECKING blocks for type-only imports satisfied lint rules

What could be improved:
- The `_PATH_CHECK_TIMEOUT_SECONDS` constant ended up duplicated between
  validate_sandbox_diagnostics.py and validate_path_checks.py. A better approach
  might be a shared constants module, but this would have added complexity for
  marginal benefit.

Lessons learned:
- When encountering duplicate functions, investigate usage patterns before
  assuming consolidation is correct. Sometimes duplicates serve distinct purposes.
- Module extraction benefits from clear dependency analysis upfront. Creating a
  dependency graph before extraction would have made the ordering decision more
  obvious.
- Tests using dynamic module loading (via fixtures) work seamlessly with
  refactored imports as long as re-exports are maintained.


## Context and orientation

The validate-linux-packages action lives in
`.github/actions/validate-linux-packages/`. The scripts directory contains
Python modules for validating Debian and RPM packages. The main orchestration
logic is in `scripts/validate_packages.py` (734 lines), which has grown to
include:

- Architecture mapping and validation (lines 57-117)
- Package discovery (locate_deb, locate_rpm, ensure_subset) (lines 119-146)
- Diagnostic collection and formatting (lines 148-312)
- Path existence and executability validation (lines 338-455)
- Sandbox installation and verification orchestration (lines 457-533)
- Metadata validation (lines 535-582)
- Public validation entry points (lines 584-734)

The module exports 10 public functions via `__all__` (line 41-51). Tests load
the module dynamically via a pytest fixture (`validate_packages_module`) in
`tests/conftest.py`. Some tests already exist in subdirectories
(`tests/locators/`, `tests/metadata/`) suggesting prior partial extraction
efforts.

The duplicate `_trim_output()` issue: two functions with the same name exist at
lines 148 and 385 with different signatures. In Python, the second definition
(line 385) overrides the first (line 148), meaning only the later single-line
variant is currently effective. However, some call sites may expect the
multi-line variant's signature (with line_limit and char_limit parameters).
This must be resolved by consolidating into a single canonical implementation
that preserves the intended behaviour for all call sites.


## Plan of work

Stage A: Audit and resolve the `_trim_output()` duplicate

- Read lines 148-174 and 385-391 to understand both signatures and their
  intended use cases.
- Search all call sites in validate_packages.py to determine which signature
  each caller expects.
- Consolidate into a single canonical `_trim_output()` implementation that
  preserves the multi-line variant's signature (line 148) with line_limit and
  char_limit parameters, since this provides the most flexibility. The
  single-line variant (line 385) can be expressed as a call to the multi-line
  variant with appropriate parameters.
- Update all call sites of the single-line variant to use the consolidated
  implementation.
- Verify behaviour remains identical and tests pass before proceeding.

Stage B: Create the five new modules

For each module, follow this pattern:

1. Create the new file in `scripts/` directory.
2. Add module docstring, imports (typing, pathlib, logging as needed).
3. Copy functions from validate_packages.py.
4. Update internal imports (e.g., ValidationError must be imported).
5. Add `__all__` listing public functions.

Module creation order (to avoid circular dependencies):

1. `validate_formatters.py` (no dependencies on other new modules)
2. `validate_sandbox_diagnostics.py` (depends on formatters)
3. `validate_path_checks.py` (depends on formatters and diagnostics)
4. `validate_arch.py` (no dependencies on other new modules)
5. `validate_locators.py` (no dependencies on other new modules)

Stage C: Update validate_packages.py

- Remove extracted functions (keep only orchestration logic).
- Add imports from the five new modules.
- Update `__all__` to re-export public functions from new modules.
- Ensure internal functions like `_exec_with_diagnostics`,
  `_install_and_verify`, `_validate_package`, `_MetadataValidators` remain.
- Keep public entry points `validate_deb_package()` and
  `validate_rpm_package()`.

Stage D: Validation

- Run `make typecheck` and fix any type errors.
- Run `make test` and ensure 635 pass, 86 skip.
- Run `make lint` and fix any violations.
- Run `make check-fmt` and fix formatting.


## Concrete steps

### Stage A: Resolve _trim_output() duplicate

1. Audit call sites:

       cd /home/user/project/.github/actions/validate-linux-packages/scripts
       grep -n "_trim_output(" validate_packages.py

   Expected: lines showing calls to both variants.

2. Rename the single-line variant (line 385) to `_trim_output_single_line()`:

   - Edit validate_packages.py line 385.
   - Update call sites at lines 419, 426 to use new name.

3. Verify compilation:

       cd /home/user/project
       python3 -m py_compile .github/actions/validate-linux-packages/scripts/validate_packages.py

   Expected: no syntax errors.

### Stage B: Create new modules

Create `validate_formatters.py`:

    Functions to extract:
    - _trim_output (line 148, multi-line variant)
    - _trim_output_single_line (line 385, renamed single-line variant)
    - _extract_process_stderr (line 166)

Create `validate_sandbox_diagnostics.py`:

    Functions to extract:
    - _execute_diagnostic_command (line 177)
    - _build_path_diagnostic_commands (line 195)
    - _format_path_diagnostics (line 221)
    - _collect_diagnostics_safely (line 243)
    - _collect_environment_details (line 269)
    - _collect_host_path_details (line 292)

Create `validate_path_checks.py`:

    Constants and functions to extract:
    - _PYTHON_FALLBACK_SCRIPT (line 27)
    - _PYTHON_FALLBACK_INTERPRETERS (line 30)
    - _PATH_CHECK_TIMEOUT_SECONDS (line 31)
    - _validate_paths_exist (line 338)
    - _validate_paths_executable (line 436)
    - _try_python_fallback (line 393)
    - _make_path_diagnostics_fn (line 356)
    - _iter_python_fallback_commands (line 365)
    - _combine_fallback_errors (line 370)

Create `validate_arch.py`:

    Functions to extract:
    - _HOST_ARCH_ALIAS_MAP (line 57)
    - _host_architectures (line 72)
    - _should_skip_sandbox (line 81)
    - acceptable_rpm_architectures (line 99)
    - rpm_expected_architecture (line 114)

Create `validate_locators.py`:

    Functions to extract:
    - locate_deb (line 119)
    - locate_rpm (line 129)
    - ensure_subset (line 139)

### Stage C: Update validate_packages.py

1. Remove extracted functions.
2. Add imports at top:

       from validate_arch import (
           _HOST_ARCH_ALIAS_MAP,
           _host_architectures,
           _should_skip_sandbox,
           acceptable_rpm_architectures,
           rpm_expected_architecture,
       )
       from validate_formatters import (
           _extract_process_stderr,
           _trim_output,
           _trim_output_single_line,
       )
       from validate_locators import (
           ensure_subset,
           locate_deb,
           locate_rpm,
       )
       from validate_path_checks import (
           _PATH_CHECK_TIMEOUT_SECONDS,
           _combine_fallback_errors,
           _iter_python_fallback_commands,
           _make_path_diagnostics_fn,
           _try_python_fallback,
           _validate_paths_executable,
           _validate_paths_exist,
       )
       from validate_sandbox_diagnostics import (
           _build_path_diagnostic_commands,
           _collect_diagnostics_safely,
           _collect_environment_details,
           _collect_host_path_details,
           _execute_diagnostic_command,
           _format_path_diagnostics,
       )

3. Update `__all__` to re-export public functions:

       __all__ = [
           "DebMetadata",
           "RpmMetadata",
           "acceptable_rpm_architectures",
           "ensure_subset",
           "locate_deb",
           "locate_rpm",
           "rpm_expected_architecture",
           "validate_deb_package",
           "validate_rpm_package",
       ]

### Stage D: Validation

Run all checks:

    cd /home/user/project
    make typecheck

Expected output: "All checks passed!" (may have 1 pre-existing warning in
windows-package).

    make test

Expected output: "635 passed, 86 skipped"

    make lint

Expected output: "All checks passed!"

    make check-fmt

Expected output: "173 files already formatted"


## Validation and acceptance

Quality criteria:

- Tests: `make test` passes with 635 passed, 86 skipped (same as baseline).
- Typecheck: `make typecheck` passes with no new errors.
- Lint: `make lint` passes with no violations.
- Formatting: `make check-fmt` shows all files formatted.
- Import compatibility: validate_cli.py and all test files continue importing
  from validate_packages without changes.

Quality method:

    cd /home/user/project
    make check-fmt && make lint && make typecheck && make test


## Idempotence and recovery

All steps are idempotent:

- Creating new modules: if the file exists, overwrite with correct content.
- Updating imports: editing is idempotent.
- Running tests: safe to repeat.

Recovery: if a stage fails, revert the changes to that stage only and retry
with corrections. Use git to track intermediate states.


## Artifacts and notes

(To be filled during implementation with key transcripts and observations)


## Interfaces and dependencies

New modules will have these public interfaces (exported via `__all__`):

`validate_formatters.py`:
- `_trim_output(output: str, *, line_limit: int = 5, char_limit: int = 400) -> str`
- `_extract_process_stderr(error: BaseException | None) -> str | None`

`validate_arch.py`:
- `acceptable_rpm_architectures(arch: str) -> set[str]`
- `rpm_expected_architecture(arch: str) -> str`

`validate_locators.py`:
- `locate_deb(package_dir: Path, package_name: str, version: str, release: str) -> Path`
- `locate_rpm(package_dir: Path, package_name: str, version: str, release: str) -> Path`
- `ensure_subset(expected: Collection[str], actual: Collection[str], label: str) -> None`

`validate_sandbox_diagnostics.py`:
- All functions are internal (prefixed with `_`); no public exports needed.

`validate_path_checks.py`:
- All functions are internal (prefixed with `_`); no public exports needed.

Dependencies flow:
- validate_packages imports from all five new modules.
- New modules import from existing modules (validate_exceptions,
  validate_helpers, validate_metadata, validate_polythene).
- No circular dependencies.
