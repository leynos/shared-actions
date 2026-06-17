# Write Docstrings for Public APIs (6.1.1)

This ExecPlan (execution plan) is a living document. The sections `Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: DRAFT


## Purpose / big picture

This plan adds comprehensive docstrings to public APIs in the correlation ID middleware feature being introduced on this branch. The goal is to ensure all exported functions, classes, and constants have clear, well-formed documentation that follows Python conventions (PEP 257 + Google Style) and integrates seamlessly with documentation generation tools like pdoc and Sphinx.

Upon completion, users will be able to:
- Access clear API documentation via Python's built-in `pydoc` tool
- Generate professional HTML documentation using pdoc or Sphinx
- Understand each component's purpose, parameters, return values, exceptions, and usage patterns
- Navigate cross-referenced documentation showing how components interact

Success is observable through: (1) all targeted APIs having non-empty, well-formed docstrings; (2) zero lint/type/test failures; (3) `pydoc stage_common.pipeline` producing readable output covering all public APIs; (4) generated HTML documentation being complete and error-free.


## Constraints

Hard invariants that must hold throughout implementation. These are not suggestions; violation requires escalation, not workarounds.

- **Google Style format**: All docstrings must follow Google Style (PEP 257 compliant) as established in existing codebase docstrings.
- **Backward compatibility**: No changes to public API signatures, import paths, or behaviour; docstrings are documentation only.
- **No breaking tests**: All existing tests must continue passing; new docstrings must not alter code execution paths.
- **Private vs public**: Private symbols (leading underscore) may use inline comments; public exported symbols in `__all__` require full docstrings.
- **Example accuracy**: All Examples in docstrings must be syntactically correct and produce correct output if run as doctests.
- **No incomplete sections**: If a section (Args, Returns, Raises, Attributes) is started, it must be complete and accurate.


## Tolerances (exception triggers)

Thresholds that trigger escalation when breached. These define the boundaries of autonomous action, not quality criteria.

- **Scope**: If implementation requires docstring changes to more than 15 files or 300 lines of net additions, stop and escalate.
- **Unknown APIs**: If CorrelationIDMiddleware or _CORR_ID_CONTEXT_KEY are not found where expected, stop and clarify design before proceeding.
- **Test failures**: If any single red test fails for reasons other than missing docstrings (e.g., import errors, syntax errors), stop and escalate.
- **Style conflicts**: If existing docstrings use NumPy Style and project leads prefer NumPy over Google Style, stop and escalate for guidance on unification approach.
- **Tool unavailability**: If pdoc/Sphinx cannot be installed or run to generate docs, stop and escalate before Stage 5.
- **Iterations**: If docstring validation tests still fail after 2 complete implementation passes (all stages 2–4 redone), stop and escalate.


## Risks

Known uncertainties that might affect the plan. Identify these upfront and update as work proceeds. Each risk should note severity, likelihood, and mitigation or contingency.

- **Risk**: CorrelationIDMiddleware class may not yet exist in the codebase and may require design/implementation as part of this plan.
  **Severity**: High
  **Likelihood**: Medium (exploration suggests it will be introduced on this branch)
  **Mitigation**: Clarify class design and intent upfront. If class does not exist, coordinate with feature branch owner on class signature and behaviour before documenting. Add placeholder with rationale to Decision Log.

- **Risk**: Existing docstrings use mixed styles (NumPy Parameters/Returns in normalize_input_env, Google Style elsewhere), creating consistency risk.
  **Severity**: Medium
  **Likelihood**: High (codebase inspection found mixed styles)
  **Mitigation**: This plan targets Google Style. If consistency requires converting existing docstrings, add to Scope at escalation point. For now, new docstrings follow Google Style only.

- **Risk**: Examples with external dependencies or side effects (e.g., file I/O, network calls) may be brittle or unsafe as doctests.
  **Severity**: Medium
  **Likelihood**: Medium
  **Mitigation**: Mark Examples as pseudocode or use `# doctest: +SKIP` for complex cases. Ensure Examples are clear even if not directly runnable.

- **Risk**: Private symbols (_CORR_ID_CONTEXT_KEY) may be considered implementation detail; extensive documentation could mislead users into treating them as public API.
  **Severity**: Low
  **Likelihood**: Low
  **Mitigation**: Private constants use inline comments only (short, focused). Document context only in public class/function docstrings that reference the private constant.

- **Risk**: Docstring validation tooling (pydocstyle, flake8-docstrings) may not be installed, requiring custom pytest fixture creation.
  **Severity**: Low
  **Likelihood**: Medium
  **Mitigation**: Create lightweight custom validation fixture in tests/test_docstring_coverage.py using ast.parse. Include in Stage 1 red tests.


## Progress

Use a list with checkboxes to summarise granular steps. Every stopping point must be documented here, even if it requires splitting a partially completed task into two ("done" vs. "remaining"). This section must always reflect the actual current state of the work.

- [ ] Stage 1: Red Tests & Validation Infrastructure
- [ ] Stage 2: Implement Docstrings for Data Classes (StageEnv, ResolvedArtefact, StagingState)
- [ ] Stage 3: Implement Docstrings for Constants & Middleware (RESERVED_OUTPUT_KEYS, CorrelationIDMiddleware, _CORR_ID_CONTEXT_KEY)
- [ ] Stage 4: Validation & Cross-Module Consistency
- [ ] Stage 5: Documentation Generation & Tool Integration


## Surprises & discoveries

Unexpected findings during implementation that were not anticipated as risks. Document with evidence so future work benefits.

(None recorded yet.)


## Decision log

Record every significant decision made while working on the plan. Include decisions to escalate, decisions on ambiguous requirements, and design choices.

- **Decision**: Google Style (PEP 257) chosen as the docstring format standard.
  **Rationale**: Excellent readability in source code, balances detail with brevity, integrates well with pydoc and Sphinx+Napoleon extension. Aligns with most existing docstrings in the codebase (actions_common.py, cargo_utils.py, cmd_utils.py).
  **Date/Author**: 2026-06-17 / Wyvern agent research phase.

- **Decision**: CorrelationIDMiddleware will be documented as a new class, with assumption that design/implementation happens as part of introducing the feature on this branch.
  **Rationale**: Exploration found the class absent from current codebase but mentioned in roadmap item 6.1.1. Documentation should follow implementation; if design changes, docstrings are updated during Implementation phase.
  **Date/Author**: 2026-06-17 / Planning phase.

- **Decision**: Private symbols (_CORR_ID_CONTEXT_KEY) documented with inline comments only; no full docstring.
  **Rationale**: PEP 257 and Google Style conventions recommend inline comments for module-level constants. Leading underscore signals private/internal; extensive documentation could mislead users. Inline comment should clarify purpose and context dict key name.
  **Date/Author**: 2026-06-17 / Planning phase.

- **Decision**: Custom pytest fixture for docstring validation (Stage 1) rather than external tool dependency.
  **Rationale**: Keeps dependencies minimal, allows customization for this project's conventions, and integrates naturally with existing pytest infrastructure.
  **Date/Author**: 2026-06-17 / Planning phase.


## Outcomes & retrospective

Summarize outcomes, gaps, and lessons learned at major milestones or at completion. Compare the result against the original purpose. Note what would be done differently next time.

(To be completed on plan completion.)


## Context and orientation

The project is a GitHub Actions monorepo with Python modules at the root level (`actions_common.py`, `bool_utils.py`, `cargo_utils.py`, `cmd_utils.py`, `cmd_utils_importer.py`) and structured packages under `.github/actions/`. The branch being worked on (`6-1-1-docstrings-for-public-apis`) introduces a correlation ID middleware feature for distributed tracing.

**Key files touched by this plan**:
- `.github/actions/stage-release-artefacts/scripts/stage_common/pipeline.py` — contains CorrelationIDMiddleware (new), StageEnv, ResolvedArtefact, StagingState, and _CORR_ID_CONTEXT_KEY
- `.github/actions/stage-release-artefacts/scripts/stage_common/output.py` — contains RESERVED_OUTPUT_KEYS constant
- `tests/test_docstring_coverage.py` — new file, docstring validation test suite (Stage 1)

**Testing infrastructure**: pytest (>=8.0, <9.0), with asyncio fixtures, hypothesis, pytest-bdd, syrupy. Tests live in per-action `tests/` subdirectories and at project root under `tests/`. Run via `make test`.

**Quality gates**: `make check-fmt`, `make typecheck`, `make lint`, `make test` must all pass before commit.

**Documentation tools available**: Python's built-in `pydoc` module. pdoc and Sphinx can be installed if needed for Stage 5.

**Existing docstring examples**: Functions like `normalize_input_env()` in `actions_common.py` use Google Style with Parameters/Returns sections. Classes like `RunResult` in `cmd_utils.py` use one-liner + docstring. Existing docstrings are the baseline for consistency.


## Plan of work

### Stage 1: Red Tests & Validation Infrastructure

Create a pytest test suite to validate docstring presence, format, and completeness. This establishes a baseline of failures and defines acceptance criteria.

**Files to create**:
- `tests/test_docstring_coverage.py` — New test module with docstring validation fixtures.

**Tasks**:

1. Create `tests/test_docstring_coverage.py` with the following fixtures:
   - `load_docstrings_from_module(module_name)` — Use `ast.parse()` to extract all public APIs and their docstrings.
   - `validate_google_style_structure(docstring)` — Check for summary line (≤79 chars, ends with period, imperative mood), one blank line separator, and standard sections (Args, Returns, Raises, Attributes, Example, Note).
   - `check_docstring_presence(module, whitelist_private=True)` — Assert all public APIs in `__all__` have non-empty `__doc__` (min 20 chars). Private symbols (leading underscore) are exempt unless explicitly required.
   - `score_docstring_completeness(docstring, symbol_kind)` — For functions, require [Summary, Args, Returns, Example] sections. For classes, require [Summary, Attributes, Example]. For constants, require inline comment or docstring. Return a tuple (found_sections, required_sections, score %).

2. Implement a parametrized test `test_public_api_docstrings()` that:
   - Loads all symbols from `stage_common.pipeline` and `stage_common.output`.
   - For each public symbol, runs presence + format + completeness checks.
   - Reports missing sections per API (e.g., "StageEnv missing: Attributes, Example").

3. Add a test helper `test_docstring_examples_syntax()` that:
   - Extracts code blocks from Examples sections.
   - Checks syntax validity (compile with `compile()` or `ast.parse()`).
   - Reports any syntax errors.

4. Define acceptance criteria in a pytest marker `@pytest.mark.docstring_validation`:
   - All 7 targeted APIs pass presence check (non-empty, ≥20 chars).
   - All function/class docstrings pass format check (Google Style structure).
   - Completeness score ≥80% for each symbol type.
   - No syntax errors in Examples.

5. Run baseline red test:
   ```bash
   pytest tests/test_docstring_coverage.py -v -m docstring_validation
   ```
   Expected: 7 APIs fail (StageEnv, ResolvedArtefact, StagingState, CorrelationIDMiddleware, RESERVED_OUTPUT_KEYS, _CORR_ID_CONTEXT_KEY, plus any others flagged).

**Validation**: Red test suite runs without errors and clearly identifies which symbols are missing docstrings and which sections are incomplete. Output examples:
```
FAILED test_docstring_coverage.py::test_public_api_docstrings[StageEnv] - AssertionError: Missing sections: Attributes, Example
FAILED test_docstring_coverage.py::test_public_api_docstrings[ResolvedArtefact] - AssertionError: Missing sections: Attributes, Example
...
```

### Stage 2: Implement Docstrings for Data Classes

Add comprehensive docstrings to StageEnv, ResolvedArtefact, and StagingState classes in `pipeline.py`. These are immutable/mutable data containers at the core of the pipeline logic.

**File**: `.github/actions/stage-release-artefacts/scripts/stage_common/pipeline.py`

**Tasks**:

1. **StageEnv** — Frozen immutable dataclass. Add docstring above class definition:
   ```python
   """Immutable staging environment container.

   Holds the configuration context for a single staging pipeline invocation.
   Carries staging directory, pipeline config, and optional context dict for
   passing request-scoped state (e.g., correlation IDs for tracing).

   Attributes:
       config: StagingConfig instance with artefact definitions and paths.
       staging_dir: pathlib.Path to the directory where artefacts are staged.
       context: Optional dict for passing state through the pipeline. Keys
           should use RESERVED_OUTPUT_KEYS naming conventions. Special key
           _CORR_ID_CONTEXT_KEY holds correlation ID for distributed tracing.

   Example:
       >>> env = StageEnv(config=cfg, staging_dir=Path('/tmp/stage'))
       >>> env.context['_correlation_id'] = 'abc-123'

   Note:
       StageEnv is frozen (immutable). Create a new instance to modify values.
       The context dict is mutable and can be updated in-place; treat as
       thread-local or use locks in concurrent environments.
   """
   ```

2. **ResolvedArtefact** — Immutable dataclass linking config to resolved paths:
   ```python
   """Resolved artefact source and destination paths.

   Links a single ArtefactConfig to its resolved source and destination paths.
   Created during _collect_artefacts and passed through the staging pipeline.

   Attributes:
       artefact: ArtefactConfig instance describing the artefact.
       source: pathlib.Path to the source file or directory.
       destination: pathlib.Path to the destination in staging_dir.

   Example:
       >>> resolved = ResolvedArtefact(
       ...     artefact=config.artefacts[0],
       ...     source=Path('/build/bin/tool'),
       ...     destination=Path('/tmp/stage/bin/tool')
       ... )
       >>> resolved.source.exists()
       True

   Note:
       ResolvedArtefact is immutable; it represents a snapshot at
       resolution time. Source/destination paths may diverge if files
       move on disk during staging.
   """
   ```

3. **StagingState** — Mutable state accumulator:
   ```python
   """Mutable staging state accumulator.

   Tracks artefacts staged, outputs prepared, checksums computed, and
   artefacts skipped during a staging pipeline run.

   Attributes:
       staged_paths: Dict[str, pathlib.Path] mapping artefact IDs to their
           staged destinations (populated during stage_artefacts).
       outputs: Dict[str, str] for GitHub Actions workflow output key-value
           pairs (populated by prepare_output_data).
       checksums: Dict[str, str] mapping staged paths to computed checksums
           (populated if digest_algorithm is specified in config).
       skipped_artefacts: List[str] of artefact IDs that were skipped
           (e.g., due to unmatched glob patterns).

   Example:
       >>> state = StagingState()
       >>> state.staged_paths['tool'] = Path('/tmp/stage/bin/tool')
       >>> state.outputs['paths'] = '/tmp/stage'
       >>> state.checksums['/tmp/stage/bin/tool'] = 'abc123...'

   Note:
       StagingState is mutable; modifications are in-place. Not thread-safe;
       use with locks or thread-local storage in concurrent pipelines.
   """
   ```

4. Verify all three docstrings:
   - Match Attributes to actual dataclass fields (use `@dataclass` inspection).
   - Use imperative mood in summary (e.g., "Holds", "Links", "Tracks").
   - End summary with period.
   - Include Example with real-like usage patterns.
   - Include Note with design rationale or gotchas.

5. Run validation: `pytest tests/test_docstring_coverage.py::test_public_api_docstrings -k "StageEnv or ResolvedArtefact or StagingState" -v`
   Expected: All three pass presence + format + completeness checks.

6. Run gates: `make lint`, `make typecheck`, `make test`
   Expected: No new lint/type errors. All tests pass.

**Validation**: Red test suite shows 3 APIs passing. No lint or type errors.

### Stage 3: Implement Docstrings for Constants & Middleware

Add docstrings to RESERVED_OUTPUT_KEYS constant, CorrelationIDMiddleware class, and _CORR_ID_CONTEXT_KEY private constant.

**Files**:
- `.github/actions/stage-release-artefacts/scripts/stage_common/output.py` (RESERVED_OUTPUT_KEYS)
- `.github/actions/stage-release-artefacts/scripts/stage_common/pipeline.py` (CorrelationIDMiddleware, _CORR_ID_CONTEXT_KEY)

**Tasks**:

1. **RESERVED_OUTPUT_KEYS** — In `output.py`, add docstring above constant definition:
   ```python
   """Frozenset of reserved GitHub Actions workflow output key names.

   These keys are used internally by the staging pipeline for framework-level
   outputs (e.g., 'staging_dir', 'status'). User-defined outputs must avoid
   collision with these names; see validate_no_reserved_key_collisions().

   Example:
       >>> 'status' in RESERVED_OUTPUT_KEYS
       True
       >>> 'my_custom_key' in RESERVED_OUTPUT_KEYS
       False

   Note:
       Keys are case-sensitive. Reserved names help prevent accidental
       override of framework outputs in GitHub Actions job contexts.
   """
   ```

2. **CorrelationIDMiddleware** — In `pipeline.py`, add docstring above class definition. This is a new class introduced on this branch; document based on feature design:
   ```python
   """Middleware for correlation ID propagation through the staging pipeline.

   Manages correlation ID context for distributed tracing. Stores and retrieves
   a unique request ID in StageEnv.context using the _CORR_ID_CONTEXT_KEY.
   Enables tracing of artefact staging operations across multiple services.

   Attributes:
       None (stateless middleware; state is carried in StageEnv.context).

   Example:
       >>> middleware = CorrelationIDMiddleware()
       >>> env = StageEnv(config=cfg, staging_dir=Path('/tmp'), context={})
       >>> middleware.set_correlation_id(env, 'trace-123')
       >>> middleware.get_correlation_id(env)
       'trace-123'

   Note:
       Correlation IDs are stored in StageEnv.context dict, not on the
       middleware instance. Multiple pipelines can share a middleware instance
       if they use separate StageEnv instances. Not thread-safe; use with
       locks if sharing context across threads.
   """
   ```

3. **_CORR_ID_CONTEXT_KEY** — In `pipeline.py`, add inline comment (private symbol, minimal doc):
   ```python
   _CORR_ID_CONTEXT_KEY = '_correlation_id'  # Private context dict key for storing correlation ID.
   ```

4. Verify docstrings:
   - RESERVED_OUTPUT_KEYS content is accurate (check actual frozenset values).
   - CorrelationIDMiddleware docstring matches class design and methods.
   - Example code is syntactically correct and realistic.
   - Private constant comment is concise and clear.

5. Run validation: `pytest tests/test_docstring_coverage.py::test_public_api_docstrings -k "RESERVED_OUTPUT_KEYS or CorrelationIDMiddleware" -v`
   Expected: Both pass checks.

6. Run gates: `make lint`, `make typecheck`, `make test`
   Expected: No errors.

**Validation**: All 7 targeted APIs now have docstrings. Red test suite shows 7/7 passing. No lint or type errors.

### Stage 4: Validation & Cross-Module Consistency

Verify all new docstrings are consistent, complete, and integrated correctly. Run full test suite and manual inspection.

**Tasks**:

1. Run full validation suite:
   ```bash
   pytest tests/test_docstring_coverage.py -v -m docstring_validation
   ```
   Expected: All tests pass (0 failures, 7+ passed).

2. Run quality gates:
   ```bash
   make check-fmt && make typecheck && make lint && make test
   ```
   Expected: All commands exit 0 with no errors.

3. Manually inspect docstrings in both files:
   - Read each docstring in source code (not just test output).
   - Verify Args/Returns/Raises format is consistent with existing docstrings in the codebase.
   - Confirm Examples are realistic and Examples section exists for every class and function.
   - Check Attributes lists match actual dataclass fields (use `python -c "from stage_common.pipeline import StageEnv; print(StageEnv.__dataclass_fields__.keys())"` to verify).

4. Check cross-references:
   - CorrelationIDMiddleware docstring mentions _CORR_ID_CONTEXT_KEY.
   - RESERVED_OUTPUT_KEYS docstring references validate_no_reserved_key_collisions().
   - StageEnv docstring mentions _CORR_ID_CONTEXT_KEY as special key in context dict.

5. Verify Google Style consistency:
   - All summary lines end with period.
   - All summary lines use imperative mood (e.g., "Holds", "Implements", "Store").
   - All summary lines are ≤79 characters.
   - All section headers use standard names (Args, Returns, Raises, Attributes, Example, Note).
   - All sections are properly indented with blank lines between sections.

6. Test pydoc readability:
   ```bash
   python -m pydoc stage_common.pipeline | head -100
   python -m pydoc stage_common.output | head -50
   ```
   Expected output should be readable, with docstrings clearly presented. No rendering errors or truncation.

7. Optional: Test with help() in Python REPL:
   ```python
   from stage_common.pipeline import CorrelationIDMiddleware
   help(CorrelationIDMiddleware)
   ```
   Expected: Clear, readable output with all sections visible.

**Validation**: All tests pass. All gates pass. pydoc output is readable. Manual inspection finds no inconsistencies or errors.

### Stage 5: Documentation Generation & Tool Integration

Test docstrings with documentation generation tools and validate they are ready for external consumption.

**Tasks**:

1. Check pdoc availability:
   ```bash
   pip list | grep pdoc || pip install pdoc
   ```

2. Generate API documentation for stage_common package:
   ```bash
   pdoc -o /tmp/pdoc_output stage_common
   ```
   Expected: HTML files in `/tmp/pdoc_output/` with `stage_common/index.html` as entry point.

3. Open generated HTML and inspect:
   - Navigate to `stage_common/pipeline.html` and verify all classes (StageEnv, ResolvedArtefact, StagingState, CorrelationIDMiddleware) appear with full docstrings.
   - Check that Attributes, Parameters, and Examples sections are clearly formatted.
   - Verify no rendering errors or broken cross-references.
   - Navigate to `stage_common/output.html` and verify RESERVED_OUTPUT_KEYS appears with documentation.

4. Validate that all public APIs in `__all__` (if defined) appear in generated docs:
   ```bash
   grep -A 10 "__all__" stage_common/__init__.py
   ```
   For each symbol in `__all__`, confirm it appears in the generated HTML.

5. Test pydoc rendering:
   ```bash
   python -m pydoc -w stage_common.pipeline  # Generates stage_common.pipeline.html
   ```
   Expected: HTML file with all docstrings rendered correctly.

6. If Sphinx is available, generate Sphinx docs (optional):
   ```bash
   sphinx-quickstart -q docs/sphinx_test
   # Edit conf.py to add 'sphinx.ext.napoleon' extension
   # Run: sphinx-build -b html docs/sphinx_test /tmp/sphinx_output
   ```
   Expected: Sphinx rendering of docstrings with Napoleon extension handling Google Style.

7. Final validation:
   - Generated HTML docs are readable and complete.
   - All 7 targeted APIs appear with full docstrings.
   - No rendering errors or broken links in generated docs.
   - Examples are clearly presented (some may not be executable in HTML, but should be readable).

**Validation**: Generated HTML documentation is complete and error-free. All public APIs are documented and render correctly.


## Concrete steps

State the exact commands to run and where to run them (working directory). When a command generates output, show a short expected transcript so the reader can compare. This section must be updated as work proceeds.

### Stage 1 Setup

Working directory: Repository root (`/tmp/lody-title-agent`).

1. Create the test file:
   ```bash
   touch tests/test_docstring_coverage.py
   ```

2. Write the validation fixtures (see full code in separate section below).

3. Run red tests to establish baseline:
   ```bash
   pytest tests/test_docstring_coverage.py -v -m docstring_validation --tb=short
   ```

   Expected output (sample):
   ```
   test_docstring_coverage.py::test_public_api_docstrings[StageEnv] FAILED - AssertionError: Docstring missing
   test_docstring_coverage.py::test_public_api_docstrings[ResolvedArtefact] FAILED - AssertionError: Docstring missing
   test_docstring_coverage.py::test_public_api_docstrings[StagingState] FAILED - AssertionError: Docstring missing
   test_docstring_coverage.py::test_public_api_docstrings[CorrelationIDMiddleware] FAILED - AssertionError: Docstring missing
   test_docstring_coverage.py::test_public_api_docstrings[RESERVED_OUTPUT_KEYS] FAILED - AssertionError: Docstring missing
   test_docstring_coverage.py::test_public_api_docstrings[_CORR_ID_CONTEXT_KEY] SKIPPED (private symbol)
   ```

### Stage 2–4 Implementation

(Steps follow the detailed tasks above; run gates after each stage.)

### Stage 5 Documentation Generation

1. Ensure pdoc is available:
   ```bash
   python -m pip install pdoc
   ```

2. Generate HTML documentation:
   ```bash
   pdoc -o /tmp/docstrings_output stage_common
   ```

3. List generated files:
   ```bash
   find /tmp/docstrings_output -name "*.html"
   ```

   Expected:
   ```
   /tmp/docstrings_output/stage_common/index.html
   /tmp/docstrings_output/stage_common/pipeline.html
   /tmp/docstrings_output/stage_common/output.html
   /tmp/docstrings_output/stage_common/config.html
   /tmp/docstrings_output/stage_common/errors.html
   /tmp/docstrings_output/stage_common/environment.html
   /tmp/docstrings_output/stage_common/resolution.html
   ```

4. Inspect generated pipeline.html (manually or via browser):
   - Verify CorrelationIDMiddleware, StageEnv, ResolvedArtefact, StagingState sections are present.
   - Check that docstrings are fully rendered with all subsections.

5. Test pydoc:
   ```bash
   python -m pydoc stage_common.pipeline | head -80
   ```

   Expected output includes class definitions with docstrings:
   ```
   class CorrelationIDMiddleware
    |  Middleware for correlation ID propagation through the staging pipeline.
    |
    |  Manages correlation ID context for distributed tracing. Stores and
    |  retrieves a unique request ID in StageEnv.context using the
    |  _CORR_ID_CONTEXT_KEY. Enables tracing of artefact staging operations
    |  across multiple services.
    |
    | ...
   ```


## Validation and acceptance

### Acceptance criteria

- All 7 targeted APIs (StageEnv, ResolvedArtefact, StagingState, CorrelationIDMiddleware, RESERVED_OUTPUT_KEYS, _CORR_ID_CONTEXT_KEY, plus any others flagged) have complete docstrings.
- All docstrings follow Google Style (PEP 257) with correct structure: summary line, blank line, body with Args/Returns/Attributes/Example/Note sections as appropriate.
- All Examples are syntactically correct and realistic.
- All gates pass: `make check-fmt`, `make typecheck`, `make lint`, `make test`.
- Generated documentation (pdoc HTML) renders without errors and is readable.
- `pydoc stage_common.pipeline` and `pydoc stage_common.output` produce readable output with all docstrings visible.

### Red-Green-Refactor evidence

**Red Phase** (Stage 1):
- Command: `pytest tests/test_docstring_coverage.py -v -m docstring_validation`
- Expected failure: 7+ tests FAILED (missing docstrings for targeted APIs).
- Proof: Test output clearly lists which symbols are missing docstrings.

**Green Phase** (Stages 2–3):
- Command: Same as above, run after each docstring addition.
- Expected: Tests transition from FAILED to PASSED as docstrings are added.
- Proof: Test output shows "PASSED" for each implemented symbol.

**Refactor Phase** (Stage 4):
- Command: `make check-fmt && make typecheck && make lint && make test`
- Expected: All commands exit 0. No lint/type/test errors.
- Proof: Gate commands pass.

### Test suite commands

```bash
# Run docstring validation tests only
pytest tests/test_docstring_coverage.py -v -m docstring_validation

# Run full test suite (unit + validation)
make test

# Run type checking
make typecheck

# Run linting
make lint

# Run formatting check
make check-fmt
```

### Quality criteria

1. **Tests**: All existing pytest tests pass. New docstring validation tests pass.
2. **Lint/typecheck**: `make lint` and `make typecheck` produce no errors.
3. **Docstring completeness**: 100% of targeted public APIs have docstrings meeting completeness scores ≥80%.
4. **Documentation rendering**: `pydoc` and `pdoc` render all docstrings correctly without errors.
5. **Google Style adherence**: All docstrings follow Google Style conventions (summary, sections, indentation, punctuation).

### Quality method

1. Run red test suite to establish baseline.
2. Implement docstrings incrementally (Stage 2–3), running green test after each symbol.
3. After all docstrings implemented, run full validation suite (Stage 4).
4. Run all quality gates (`make check-fmt`, `make typecheck`, `make lint`, `make test`).
5. Generate and inspect HTML documentation with pdoc (Stage 5).
6. Manually inspect docstrings in source code and with `pydoc` for readability.


## Idempotence and recovery

All steps in this plan are idempotent and can be re-run safely:

- Creating/modifying docstrings is safe; re-running does not change behaviour.
- Validation tests can be re-run without side effects.
- Running `make check-fmt` multiple times is safe.

If a stage fails midway:

1. Identify which symbols still lack docstrings (run red test suite).
2. Resume from the point where failures occurred (do not restart from Stage 1).
3. Document the failure in `Surprises & Discoveries` section.
4. Re-run gates after resuming.

**Rollback**: If docstrings are accidentally malformed, revert the affected file(s) with `git checkout -- <file>` and re-implement. No data loss occurs.


## Artifacts and notes

### Test fixture code (tests/test_docstring_coverage.py outline)

```python
import ast
import pytest
from pathlib import Path
from stage_common import pipeline, output


@pytest.mark.docstring_validation
class TestDocstringCoverage:
    """Validate docstring presence, format, and completeness."""

    PUBLIC_APIS_TO_CHECK = [
        ('stage_common.pipeline', 'StageEnv'),
        ('stage_common.pipeline', 'ResolvedArtefact'),
        ('stage_common.pipeline', 'StagingState'),
        ('stage_common.pipeline', 'CorrelationIDMiddleware'),
        ('stage_common.output', 'RESERVED_OUTPUT_KEYS'),
    ]

    @pytest.mark.parametrize('module_name,symbol_name', PUBLIC_APIS_TO_CHECK)
    def test_public_api_docstrings(self, module_name, symbol_name):
        """Check docstring presence and Google Style format."""
        module = __import__(module_name, fromlist=[symbol_name])
        symbol = getattr(module, symbol_name)
        
        # Presence check
        assert symbol.__doc__, f"{symbol_name} is missing docstring"
        assert len(symbol.__doc__) >= 20, f"{symbol_name} docstring too short"
        
        # Format check (summary line)
        lines = symbol.__doc__.strip().split('\n')
        summary = lines[0]
        assert summary.endswith('.'), f"Summary must end with period: {summary}"
        assert len(summary) <= 79, f"Summary too long (>{79}): {summary}"
        
        # Completeness check (mock; real implementation uses ast.parse)
        doc_lower = symbol.__doc__.lower()
        if symbol_name in ['StageEnv', 'ResolvedArtefact', 'StagingState']:
            assert 'attributes:' in doc_lower, f"{symbol_name} missing Attributes section"
            assert 'example:' in doc_lower, f"{symbol_name} missing Example section"

    def test_docstring_examples_syntax(self):
        """Ensure Examples in docstrings are syntactically valid."""
        # Extract Examples from docstrings and compile them
        for module_name, symbol_name in self.PUBLIC_APIS_TO_CHECK:
            module = __import__(module_name, fromlist=[symbol_name])
            symbol = getattr(module, symbol_name)
            if symbol.__doc__ and 'Example:' in symbol.__doc__:
                # Parse and validate syntax (mock implementation)
                assert True  # Real implementation uses ast.parse()
```

(Full implementation includes ast.parse() for robust structure validation.)


## Interfaces and dependencies

### Docstring templates (Google Style)

**Class docstring template**:
```
'''One-liner describing the class.

Extended description covering purpose, use cases, design decisions, when
to use vs. alternatives, and any important limitations.

Attributes:
    attr1: Type and description of first attribute.
    attr2: Type and description of second attribute.

Example:
    >>> instance = ClassName(param1=value)
    >>> instance.do_something()
    output

Note:
    Thread-safety, immutability, design decisions, or gotchas.
'''
```

**Function docstring template**:
```
'''Brief one-liner in imperative mood, max 79 chars, period-terminated.

Extended description explaining what, why, use cases, when to use, and
any important side effects or design decisions.

Args:
    param1: Description and type context (if not obvious from annotation).
    param2: Description. Defaults to 'value'.

Returns:
    Type description of return value. None if void.

Raises:
    ExceptionType: When raised and why.
    AnotherException: Another condition.

Example:
    >>> result = function_name(param1=value)
    >>> result
    expected_output

Note:
    Side effects, performance notes, thread-safety guarantees, or gotchas.
'''
```

**Constant docstring template (module-level)**:
```
# CONSTANT_NAME: Concise description of purpose and usage.
# If more detail needed, use a triple-quoted docstring.
```

### Required symbols

By completion, the following symbols must exist with docstrings:

- `stage_common.pipeline.CorrelationIDMiddleware` (class) — fully documented
- `stage_common.pipeline.StageEnv` (class) — fully documented
- `stage_common.pipeline.ResolvedArtefact` (class) — fully documented
- `stage_common.pipeline.StagingState` (class) — fully documented
- `stage_common.pipeline._CORR_ID_CONTEXT_KEY` (constant) — inline comment
- `stage_common.output.RESERVED_OUTPUT_KEYS` (constant) — fully documented

All existing public APIs (actions_common, bool_utils, cargo_utils, cmd_utils, etc.) should already have docstrings; this plan does not modify them unless Google Style consistency requires updates (which would trigger escalation).


## Known Unknowns & Clarifications Needed

1. **CorrelationIDMiddleware design**: If the class does not exist when implementation begins, clarify the expected interface (methods, parameters, state). Add a note to `Decision Log` with design rationale before adding docstring.

2. **Docstring style unification**: If conversion of existing mixed-style docstrings (NumPy → Google) is required for consistency, escalate at Stage 1 with scope estimate.

3. **Doctest execution**: Should Examples be runnable as doctests, or are some marked with `# doctest: +SKIP`? Clarify before Stage 2.

4. **Documentation hosting**: Should generated HTML docs be committed to the repo or only generated on-demand? Clarify before Stage 5.

