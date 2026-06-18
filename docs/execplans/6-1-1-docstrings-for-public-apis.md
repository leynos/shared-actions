# Write Docstrings for Public APIs (6.1.1)

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`,
and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: DRAFT

## Purpose / big picture

This plan adds comprehensive docstrings to public APIs in the correlation ID
middleware feature being introduced on this branch. The goal is to ensure all
exported functions, classes, and constants have clear, well-formed
documentation that follows Python conventions (PEP 257 + Google Style).

Upon completion, users will be able to:

- Access clear API documentation via Python's built-in `pydoc` tool
- Generate professional HTML documentation using pdoc or Sphinx
- Understand each component's purpose, parameters, return values, exceptions
- Navigate cross-referenced documentation

Success is observable through: (1) all targeted APIs having non-empty,
well-formed docstrings; (2) zero lint/type/test failures; (3) `pydoc
stage_common.pipeline` producing readable output; (4) generated HTML
documentation being complete and error-free.

## Constraints

Hard invariants that must hold throughout implementation. These are not
suggestions; violation requires escalation, not workarounds.

- **Google Style format**: All docstrings must follow Google Style (PEP 257
  compliant) as established in existing codebase.
- **Backward compatibility**: No changes to public API signatures, import
  paths, or behaviour; docstrings are documentation only.
- **No breaking tests**: All existing tests must continue passing.
- **Private vs public**: Private symbols (leading underscore) may use inline
  comments; public exported symbols require full docstrings.
- **Example accuracy**: All Examples must be syntactically correct.
- **No incomplete sections**: If a section is started, it must be complete
  and accurate.

## Tolerances (exception triggers)

Thresholds that trigger escalation when breached. These define the boundaries
of autonomous action, not quality criteria.

- **Scope**: If docstring changes require more than 15 files or 300 lines of
  net additions, stop and escalate.
- **Unknown APIs**: If CorrelationIDMiddleware or _CORR_ID_CONTEXT_KEY are
  not found where expected, stop and clarify design.
- **Test failures**: If any red test fails for non-docstring reasons (import
  errors, syntax errors), stop and escalate.
- **Style conflicts**: If existing docstrings use NumPy Style and project
  leads prefer NumPy, stop and escalate for guidance.
- **Tool unavailability**: If pdoc/Sphinx cannot be installed, stop and
  escalate before Stage 5.
- **Iterations**: If validation tests still fail after 2 complete passes, stop
  and escalate.

## Risks

Known uncertainties that might affect the plan. Identify these upfront and
update as work proceeds.

- **Risk**: CorrelationIDMiddleware class may not exist in codebase.
  **Severity**: High
  **Likelihood**: Medium (exploration suggests introduction on this branch)
  **Mitigation**: Clarify class design upfront. Coordinate on class signature
  and behaviour before documenting.

- **Risk**: Existing docstrings use mixed styles (NumPy and Google).
  **Severity**: Medium
  **Likelihood**: High
  **Mitigation**: Target Google Style for new docstrings only.

- **Risk**: Examples with external dependencies may be brittle as doctests.
  **Severity**: Medium
  **Likelihood**: Medium
  **Mitigation**: Use `# doctest: +SKIP` for complex cases.

- **Risk**: Private symbols may be documented extensively, misleading users.
  **Severity**: Low
  **Likelihood**: Low
  **Mitigation**: Private constants use inline comments only.

- **Risk**: Docstring validation tooling may not be installed.
  **Severity**: Low
  **Likelihood**: Medium
  **Mitigation**: Create lightweight pytest fixture using ast.parse.

## Progress

Use a list with checkboxes to summarise granular steps. Every stopping point
must be documented here. This section must always reflect the actual current
state of the work.

- [ ] Stage 1: Red Tests & Validation Infrastructure
- [ ] Stage 2: Implement Docstrings for Data Classes
- [ ] Stage 3: Implement Docstrings for Constants & Middleware
- [ ] Stage 4: Validation & Cross-Module Consistency
- [ ] Stage 5: Documentation Generation & Tool Integration

## Surprises & discoveries

Unexpected findings during implementation that were not anticipated as risks.
Document with evidence so future work benefits.

(None recorded yet.)

## Decision log

Record every significant decision made while working on the plan. Include
decisions to escalate, decisions on ambiguous requirements, and design
choices.

- **Decision**: Google Style (PEP 257) chosen as docstring format standard.
  **Rationale**: Excellent readability, balances detail with brevity,
  integrates well with pydoc and Sphinx+Napoleon. Aligns with most existing
  docstrings (actions_common.py, cargo_utils.py, cmd_utils.py).
  **Date/Author**: 2026-06-17 / Wyvern agent research phase.

- **Decision**: CorrelationIDMiddleware documented as new class with
  assumption that design/implementation happens as part of feature introduction
  on this branch.
  **Rationale**: Exploration found class absent from current codebase. If
  design changes, docstrings are updated during implementation phase.
  **Date/Author**: 2026-06-17 / Planning phase.

- **Decision**: Private symbols (_CORR_ID_CONTEXT_KEY) documented with inline
  comments only; no full docstring.
  **Rationale**: PEP 257 and Google Style recommend inline comments for
  module-level constants. Leading underscore signals private/internal.
  **Date/Author**: 2026-06-17 / Planning phase.

- **Decision**: Custom pytest fixture for docstring validation rather than
  external tool dependency.
  **Rationale**: Keeps dependencies minimal, allows customization, integrates
  naturally with existing pytest infrastructure.
  **Date/Author**: 2026-06-17 / Planning phase.

## Outcomes & retrospective

Summarize outcomes, gaps, and lessons learned at major milestones or at
completion. Compare the result against the original purpose.

(To be completed on plan completion.)

## Context and orientation

The project is a GitHub Actions monorepo with Python modules at the root
level (`actions_common.py`, `bool_utils.py`, `cargo_utils.py`,
`cmd_utils.py`, `cmd_utils_importer.py`) and structured packages under
`.github/actions/`. The branch being worked on introduces a correlation ID
middleware feature for distributed tracing.

**Key files touched by this plan**:

- `.github/actions/stage-release-artefacts/scripts/stage_common/pipeline.py`
  — CorrelationIDMiddleware (new), StageEnv, ResolvedArtefact, StagingState,
  _CORR_ID_CONTEXT_KEY
- `.github/actions/stage-release-artefacts/scripts/stage_common/output.py`
  — RESERVED_OUTPUT_KEYS constant
- `tests/test_docstring_coverage.py` — new file, docstring validation test
  suite (Stage 1)

**Testing infrastructure**: pytest (>=8.0, <9.0), with asyncio fixtures,
hypothesis, pytest-bdd, syrupy. Tests live in per-action `tests/`
subdirectories and at project root under `tests/`. Run via `make test`.

**Quality gates**: `make check-fmt`, `make typecheck`, `make lint`, `make
test` must all pass before commit.

**Existing docstring examples**: Functions like `normalize_input_env()` in
`actions_common.py` use Google Style. Classes like `RunResult` in
`cmd_utils.py` use one-liner + docstring. Existing docstrings are the
baseline for consistency.

## Plan of work

### Stage 1: Red Tests & Validation Infrastructure

Create a pytest test suite to validate docstring presence, format, and
completeness. This establishes a baseline of failures and defines acceptance
criteria.

**Files to create**: `tests/test_docstring_coverage.py` — Test module with
docstring validation fixtures.

**Key tasks**:

- Create test fixtures: `load_docstrings_from_module()`,
  `validate_google_style_structure()`, `check_docstring_presence()`,
  `score_docstring_completeness()`
- Implement parametrized test `test_public_api_docstrings()`
- Add test helper `test_docstring_examples_syntax()`
- Define acceptance criteria in pytest marker `@pytest.mark.docstring_validation`
- Run baseline red test to establish failures

**Validation**: Red test suite runs without errors and clearly identifies
which symbols are missing docstrings and which sections are incomplete.

### Stage 2: Implement Docstrings for Data Classes

Add comprehensive docstrings to StageEnv, ResolvedArtefact, and StagingState
classes in `pipeline.py`. These are immutable/mutable data containers at the
core of the pipeline logic.

**File**: `.github/actions/stage-release-artefacts/scripts/stage_common/pipeline.py`

**Key tasks**:

- Add StageEnv docstring with Attributes, Example, and Note sections
- Add ResolvedArtefact docstring with Attributes, Example, and Note sections
- Add StagingState docstring with Attributes, Example, and Note sections
- Verify Attributes match actual dataclass fields
- Run green tests for these three classes
- Run gates: `make lint`, `make typecheck`, `make test`

**Validation**: All three classes pass presence + format + completeness
checks. No lint or type errors.

### Stage 3: Implement Docstrings for Constants & Middleware

Add docstrings to RESERVED_OUTPUT_KEYS constant, CorrelationIDMiddleware
class, and _CORR_ID_CONTEXT_KEY private constant.

**Files**:

- `.github/actions/stage-release-artefacts/scripts/stage_common/output.py`
- `.github/actions/stage-release-artefacts/scripts/stage_common/pipeline.py`

**Key tasks**:

- Add RESERVED_OUTPUT_KEYS docstring in output.py
- Add CorrelationIDMiddleware docstring in pipeline.py
- Add _CORR_ID_CONTEXT_KEY inline comment in pipeline.py
- Verify docstrings are accurate and Examples are realistic
- Run green tests for these three symbols
- Run gates: `make lint`, `make typecheck`, `make test`

**Validation**: All 7 targeted APIs have docstrings. Tests show 7/7 passing.
No lint or type errors.

### Stage 4: Validation & Cross-Module Consistency

Verify all new docstrings are consistent, complete, and integrated correctly.
Run full test suite and manual inspection.

**Key tasks**:

- Run full validation suite: `pytest tests/test_docstring_coverage.py -v -m docstring_validation`
- Run quality gates: `make check-fmt && make typecheck && make lint && make test`
- Manually inspect docstrings for consistency with existing patterns
- Check cross-references between docstrings
- Verify Google Style consistency (summary lines, sections, indentation)
- Test pydoc readability: `python -m pydoc stage_common.pipeline`

**Validation**: All tests pass. All gates pass. pydoc output is readable.
Manual inspection finds no inconsistencies or errors.

### Stage 5: Documentation Generation & Tool Integration

Test docstrings with documentation generation tools and validate they are
ready for external consumption.

**Key tasks**:

- Ensure pdoc availability: `pip install pdoc`
- Generate API documentation: `pdoc -o /tmp/pdoc_output stage_common`
- Open generated HTML and inspect for readability and correctness
- Validate that all public APIs appear in generated docs
- Test pydoc rendering: `python -m pydoc -w stage_common.pipeline`
- Optionally: Generate Sphinx docs if tool is available

**Validation**: Generated HTML documentation is complete and error-free. All
public APIs are documented and render correctly.

## Concrete steps

State the exact commands to run and where to run them. Working directory:
Repository root (`/tmp/lody-title-agent`).

### Stage 1 Setup

- Create the test file:

```bash
touch tests/test_docstring_coverage.py
```

- Write the validation fixtures (see `Test fixture code` section below).

- Run red tests to establish baseline:

```bash
pytest tests/test_docstring_coverage.py -v -m docstring_validation \
  --tb=short
```

Expected: 7+ tests FAILED (missing docstrings for targeted APIs).

### Stage 2–4 Implementation

Follow the detailed tasks above; run gates after each stage.

### Stage 5 Documentation Generation

- Ensure pdoc is available:

```bash
python -m pip install pdoc
```

- Generate HTML documentation:

```bash
pdoc -o /tmp/docstrings_output stage_common
```

- List generated files:

```bash
find /tmp/docstrings_output -name "*.html"
```

- Test pydoc:

```bash
python -m pydoc stage_common.pipeline | head -80
```

## Validation and acceptance

### Acceptance criteria

- All 7 targeted APIs have complete docstrings.
- All docstrings follow Google Style (PEP 257) with correct structure.
- All Examples are syntactically correct and realistic.
- All gates pass: `make check-fmt`, `make typecheck`, `make lint`, `make
  test`.
- Generated documentation (pdoc HTML) renders without errors.
- `pydoc stage_common.pipeline` produces readable output with all docstrings
  visible.

### Red-Green-Refactor evidence

**Red Phase** (Stage 1):

- Command: `pytest tests/test_docstring_coverage.py -v -m
  docstring_validation`
- Expected: 7+ tests FAILED
- Proof: Test output clearly lists which symbols are missing docstrings

**Green Phase** (Stages 2–3):

- Command: Same as above
- Expected: Tests transition from FAILED to PASSED as docstrings are added
- Proof: Test output shows "PASSED" for each implemented symbol

**Refactor Phase** (Stage 4):

- Command: `make check-fmt && make typecheck && make lint && make test`
- Expected: All commands exit 0
- Proof: Gate commands pass

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

1. **Tests**: All existing pytest tests pass. New docstring validation tests
   pass.
2. **Lint/typecheck**: `make lint` and `make typecheck` produce no errors.
3. **Docstring completeness**: 100% of targeted public APIs have docstrings
   meeting completeness scores ≥80%.
4. **Documentation rendering**: `pydoc` and `pdoc` render all docstrings
   correctly without errors.
5. **Google Style adherence**: All docstrings follow Google Style conventions.

### Quality method

1. Run red test suite to establish baseline.
2. Implement docstrings incrementally (Stage 2–3), running green test after
   each symbol.
3. After all docstrings implemented, run full validation suite (Stage 4).
4. Run all quality gates.
5. Generate and inspect HTML documentation with pdoc (Stage 5).
6. Manually inspect docstrings in source code and with `pydoc` for
   readability.

## Idempotence and recovery

All steps in this plan are idempotent and can be re-run safely:

- Creating/modifying docstrings is safe; re-running does not change behaviour.
- Validation tests can be re-run without side effects.
- Running `make check-fmt` multiple times is safe.

If a stage fails midway:

1. Identify which symbols still lack docstrings (run red test suite).
2. Resume from the point where failures occurred.
3. Document the failure in `Surprises & Discoveries` section.
4. Re-run gates after resuming.

**Rollback**: If docstrings are accidentally malformed, revert affected
file(s) with `git checkout -- <file>` and re-implement. No data loss occurs.

## Artifacts and notes

### Test fixture code outline (tests/test_docstring_coverage.py)

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

    @pytest.mark.parametrize(
        'module_name,symbol_name',
        PUBLIC_APIS_TO_CHECK
    )
    def test_public_api_docstrings(
        self,
        module_name,
        symbol_name
    ):
        """Check docstring presence and Google Style format."""
        module = __import__(module_name, fromlist=[symbol_name])
        symbol = getattr(module, symbol_name)

        # Presence check
        assert symbol.__doc__, \
            f"{symbol_name} is missing docstring"
        assert len(symbol.__doc__) >= 20, \
            f"{symbol_name} docstring too short"

        # Format check (summary line)
        lines = symbol.__doc__.strip().split('\n')
        summary = lines[0]
        assert summary.endswith('.'), \
            f"Summary must end with period: {summary}"
        assert len(summary) <= 79, \
            f"Summary too long: {summary}"

        # Completeness check
        doc_lower = symbol.__doc__.lower()
        if symbol_name in [
            'StageEnv',
            'ResolvedArtefact',
            'StagingState'
        ]:
            assert 'attributes:' in doc_lower, \
                f"{symbol_name} missing Attributes section"
            assert 'example:' in doc_lower, \
                f"{symbol_name} missing Example section"

    def test_docstring_examples_syntax(self):
        """Ensure Examples in docstrings are syntactically valid."""
        for module_name, symbol_name in self.PUBLIC_APIS_TO_CHECK:
            module = __import__(module_name, fromlist=[symbol_name])
            symbol = getattr(module, symbol_name)
            if symbol.__doc__ and 'Example:' in symbol.__doc__:
                # Validate syntax (implementation uses ast.parse)
                assert True
```

## Interfaces and dependencies

### Docstring templates (Google Style)

**Class docstring**:

```plaintext
'''Brief one-liner describing the class.

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

**Function docstring**:

```plaintext
'''Brief one-liner in imperative mood, max 79 chars, period-terminated.

Extended description explaining what, why, use cases, when to use, and
any important side effects or design decisions.

Args:
    param1: Description and type context if not obvious from annotation.
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
    Side effects, performance notes, thread-safety guarantees, gotchas.
'''
```

**Constant docstring** (module-level):

```plaintext
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

All existing public APIs should already have docstrings; this plan does not
modify them unless Google Style consistency requires updates (which would
trigger escalation).

## Known Unknowns & Clarifications Needed

1. **CorrelationIDMiddleware design**: If the class does not exist when
   implementation begins, clarify the expected interface (methods,
   parameters, state). Add a note to `Decision Log` with design rationale
   before adding docstring.

2. **Docstring style unification**: If conversion of existing mixed-style
   docstrings (NumPy → Google) is required for consistency, escalate at
   Stage 1 with scope estimate.

3. **Doctest execution**: Should Examples be runnable as doctests, or are
   some marked with `# doctest: +SKIP`? Clarify before Stage 2.

4. **Documentation hosting**: Should generated HTML docs be committed to the
   repo or only generated on-demand? Clarify before Stage 5.
