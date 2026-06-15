# Extend Architecture Enforcement to Orchestration Code (2.4.5)

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: DRAFT


## Purpose / big picture

The Episodic podcast generation system enforces Hexagonal Architecture via Hecate,
a static import-direction checker. However, Hecate operates at the module level
and cannot validate that individual LangGraph node functions or Celery task
functions depend only on domain services and ports — never on adapter
implementations. Additionally, checkpoint payloads that serialize orchestration
state are not audited to ensure they contain only orchestration metadata and not
canonical domain objects.

This plan extends architecture enforcement to close three gaps: (1) static
validation that LangGraph nodes and Celery task modules are correctly classified
and free from adapter dependencies, (2) property-based test coverage that
checkpoint payloads remain boundary-pure under serialization and deserialization,
and (3) a lightweight runtime guard that detects when node functions are
accidentally defined outside the orchestration boundary.

After this change, running `make check` will detect LangGraph and Celery boundary
violations at lint time, running `make test` will include property-based checks
that prove checkpoint payloads preserve domain boundaries across serialization
cycles, and the orchestration graph construction will fail fast with a clear
error if a node function is accidentally declared in an adapter module.


## Constraints

Hard invariants that must hold throughout implementation.

- Do not modify the Hecate dependency version, configuration format, or the
  current group definitions in `pyproject.toml` unless justified by a concrete
  incompatibility. The existing Hecate setup is known-good and underpins all
  current architecture enforcement.

- The Episodic core domain and port contracts must not depend on orchestration
  code. The domain layer (under `episodic.canonical.domain.*`, `episodic.cost.*`,
  `episodic.llm.ports`, etc.) must remain orchestration-agnostic.

- Checkpoint serialization must remain round-trip compatible. The functions in
  `episodic/orchestration/_checkpoint_payload.py` must continue to serialize
  and deserialize checkpoint payloads without loss of information. No breaking
  changes to the checkpoint DTO schema.

- Existing production Celery tasks and LangGraph node functions must continue to
  work without modification after this change. New validation rules must detect
  *future* violations, not retroactively flag compliant code as broken.

- The validation rules must not require external dependencies beyond what is
  already pinned in `pyproject.toml` and `Makefile`. If new tooling is needed
  (e.g., `ast-grep` patterns), it must be purely additive and not alter the
  existing Hecate setup.


## Tolerances (exception triggers)

Thresholds that trigger escalation when breached.

- **Scope**: If the implementation requires changes to more than 15 files or
  more than 500 net lines of code (excluding tests and fixtures), escalate to
  review the scope.

- **New external dependencies**: If any new PyPI packages are required beyond
  those already in `pyproject.toml`, escalate before adding them.

- **Test complexity**: If property-based tests for checkpoint payloads require
  more than 300 lines of test code (fixtures, strategies, assertions), escalate
  to discuss test structure.

- **Breaking changes**: If any change causes existing tests in
  `test_architecture_enforcement.py`, `test_orchestration_langgraph_properties.py`,
  or related BDD scenarios to fail, escalate before modifying those tests.

- **Documentation gaps**: If architectural decisions cannot be recorded in ADR or
  design documents within the scope of this plan, escalate.


## Risks

Known uncertainties that might affect the plan.

- **Risk**: Dynamic imports in `episodic/orchestration/langgraph.py` (line 70)
  and `_checkpoint_payload.py` (line 28) are invisible to Hecate. A future
  developer might use `importlib.import_module()` to import from an adapter
  module, bypassing static checks.
  Severity: medium
  Likelihood: medium
  Mitigation: Document the dynamic import gap in ADR-014; add a runtime check
  at graph construction time that introspects node functions for violations.

- **Risk**: Property-based tests for checkpoint payloads may be slow if the
  Hypothesis strategy is too broad or if serialization is expensive. Tests may
  timeout in CI.
  Severity: low
  Likelihood: low
  Mitigation: Start with focused strategies that generate representative
  payloads (e.g., a bounded set of DTOs); profile test execution time before
  committing.

- **Risk**: The runtime guard at graph construction time might trigger false
  positives if node functions are wrapped or dynamically registered in ways
  that obscure their module origin.
  Severity: low
  Likelihood: low
  Mitigation: Implement the guard as a clear, inspectable check; emit a
  detailed error message if triggered; test with existing graph builders.

- **Risk**: Checkpoint payloads might contain pickle'd objects or other opaque
  serialization formats that property-based tests cannot inspect.
  Severity: medium
  Likelihood: low
  Mitigation: Audit the serialization format in `_checkpoint_payload.py` early.
  If opaque formats are used, pivot to schema-based assertions (e.g., JSON Schema
  validation) instead of type-purity property tests.


## Progress

Use a list with checkboxes to summarise granular steps.

- [ ] **Understand existing enforcement and identify gaps** (scope: research and
  documentation; no code changes).
  - [ ] Review Hecate configuration in `pyproject.toml` and understand group
    definitions.
  - [ ] Read ADR-014 and the `adopt-hecate` execplan for migration context.
  - [ ] Examine `episodic/orchestration/langgraph.py` and
    `episodic/worker/tasks.py` to understand current node/task structure.
  - [ ] Inspect `episodic/orchestration/_checkpoint_payload.py` to understand
    serialization format.
  - [ ] Document findings in this execplan and escalate if necessary.

- [ ] **Design the three-layer enforcement approach**.
  - [ ] Static layer: confirm Hecate group coverage for LangGraph and Celery
    modules is complete; document any new module prefixes that need to be added
    to group definitions.
  - [ ] Test layer: design property-based checkpoint tests using Hypothesis.
  - [ ] Runtime layer: design and prototype a lightweight guard at graph
    construction time.

- [ ] **Implement static enforcement via Hecate group review** (stage: ensure
  correct module classification).
  - [ ] Verify all LangGraph modules are under `application` group and have no
    forbidden imports.
  - [ ] Verify all Celery task modules are under `inbound_adapter` group and
    respect group rules.
  - [ ] Add a targeted Hecate test (or ast-grep pattern) that explicitly checks
    no task module imports `outbound_adapter` prefixes.
  - [ ] Run `make check-architecture` and confirm all tests pass.

- [ ] **Implement test-based checkpoint payload validation** (stage: Red-Green
  property tests).
  - [ ] Red: write a property-based test in
    `tests/test_orchestration_checkpoint_payload_properties.py` that asserts
    checkpoint round-trip preserves data and contains no adapter-scoped types.
  - [ ] Confirm test fails for the expected reason (if current payloads violate
    the property, add fixtures or accept the current state and document why).
  - [ ] Green: implement any necessary changes to payload serialization to ensure
    property holds (if needed).
  - [ ] Run the test and confirm it passes; run full test suite with
    `make test`.

- [ ] **Implement runtime guard at graph construction** (stage: lightweight
  introspection).
  - [ ] Add a guard function in `episodic/orchestration/langgraph.py` that
    inspects each node function's `__module__` attribute at graph construction
    time.
  - [ ] Guard must assert that every node function lives in a module under
    `episodic.orchestration` and raise a clear `ArchitectureBoundaryError` if
    violated.
  - [ ] Write unit tests in `tests/test_orchestration_langgraph_enforcement.py`
    that verify the guard catches violations.
  - [ ] Test that existing node functions pass the guard.
  - [ ] Run `make test` and confirm all tests pass.

- [ ] **Update documentation and ADR**.
  - [ ] Update ADR-014 to document the new three-layer enforcement approach and
    explain the scope (what is enforced, what is not, and why).
  - [ ] Update the hexagonal architecture enforcement section in
    `episodic-podcast-generation-system-design.md` to reflect the new guards.
  - [ ] Add a brief section to `docs/developers-guide.md` explaining the
    expectations for LangGraph node and Celery task modules.

- [ ] **Validation gates and final verification**.
  - [ ] Run `make check-fmt` and confirm all formatting passes.
  - [ ] Run `make lint` and confirm all linting passes (including any new rules).
  - [ ] Run `make typecheck` and confirm no type errors.
  - [ ] Run `make test` and confirm all tests pass, including new tests for
    checkpoint payloads and LangGraph guard.
  - [ ] Run `coderabbit review --agent` and address any review concerns.
  - [ ] Create a git commit with clear commit message summarizing the changes.


## Surprises & discoveries

Unexpected findings during implementation that were not anticipated as risks.
To be filled in as work proceeds.


## Decision log

Record every significant decision made while working on the plan.

- **Decision**: Use a three-layer enforcement strategy: static (Hecate group
  review), test-based (Hypothesis property tests), and runtime (lightweight
  introspection guard).
  Rationale: Hecate is already established and handles module-level enforcement
  well; property tests are the standard approach for boundary-purity validation
  in async systems; a runtime guard provides defense in depth without adding
  external dependencies.
  Date: 2026-06-15


## Outcomes & retrospective

To be completed upon finishing the plan.


## Context and orientation

The Episodic system is a podcast generation platform built on Hexagonal
Architecture using Falcon (HTTP API), Celery (background tasks), and LangGraph
(agentic orchestration). The architecture is enforced by Hecate, an open-source
static import-direction checker that reads module group definitions from
`pyproject.toml` and reports violations.

**Key files and modules**:

- `pyproject.toml` — Hecate group configuration at `[tool.hecate]` (lines
  439–514). Groups are: `composition_root`, `domain_ports`, `application`,
  `inbound_adapter`, `outbound_adapter`.
- `episodic/orchestration/langgraph.py` — LangGraph state graph builder. Nodes
  are functions that call port protocols (e.g., `protocols.PlannerPort`).
- `episodic/orchestration/_checkpoint_payload.py` — Serializes and deserializes
  checkpoint state to/from dicts. Functions:
  `_planner_result_to_payload()` and `_planner_result_from_payload()`.
- `episodic/worker/tasks.py` — Celery task definitions. Tasks use injected
  callables from `WorkerDependencies` rather than importing adapters directly.
- `episodic/worker/runtime.py` — Celery composition root.
- `episodic/orchestration/_protocols.py` — Port interfaces for orchestration
  (e.g., `CheckpointPort`, `PlannerPort`).
- ADR-014 (`docs/adr/adr-014-hexagonal-architecture-enforcement.md`) — Documents
  the existing enforcement strategy and explicitly calls out that LangGraph and
  Celery boundary checks are "not yet implemented".
- `docs/langgraph-and-celery-in-hexagonal-architecture.md` — Detailed analysis
  of architectural friction and how to maintain boundaries.

**Why this matters**:

Long-running orchestration workflows (LangGraph graphs resuming from checkpoints,
Celery tasks retrying) can introduce architectural drift: a node function or task
might accumulate direct dependencies on storage adapters or external service
clients over time, violating the domain's purity. Static enforcement (Hecate)
catches imports at the module level but cannot see into individual functions.
Test-based enforcement validates that serialized checkpoints do not leak
canonical domain objects. Runtime guards provide a fast-fail mechanism at graph
construction time.


## Plan of work

The work is structured in four stages: research and design (no code changes),
static enforcement via Hecate, test-based checkpoint validation, and runtime
guards. Each stage includes explicit validation steps.

### Stage 1: Understand existing enforcement and identify gaps

**Activities (no code changes)**:

1. Review `pyproject.toml` Hecate group definitions and confirm that all
   orchestration modules are correctly classified. Verify that:
   - `episodic.orchestration` and any submodules are in the `application` group.
   - `episodic.worker.tasks` and `episodic.worker.topology` are in the
     `inbound_adapter` group.
   - The `inbound_adapter` group rules forbid imports from `outbound_adapter`.

2. Read ADR-014 and the `adopt-hecate` execplan to understand the current
   enforcement design and the deliberate gaps (e.g., dynamic imports, node-level
   policies).

3. Inspect `episodic/orchestration/langgraph.py` to understand:
   - How node functions are defined and registered (e.g., `graph.add_node(...)`,
     `graph.add_edge(...)`).
   - What each node function depends on (ports, domain services, or adapters).
   - Where dynamic imports occur (e.g., `importlib.import_module(...)` at
     lines 70-71).

4. Inspect `episodic/orchestration/_checkpoint_payload.py` to understand:
   - The serialization format (is it JSON-compatible dicts, pickle, or other?).
   - What types are serialized into the payload.
   - Whether the payload can contain ORM objects, adapter instances, or only
     domain DTOs.

5. Document findings in this plan's `Surprises & Discoveries` section and
   escalate if any findings conflict with the stated constraints or gaps are
   larger than expected.

**Validation**: Hand-written code review of key files, no tests required.

### Stage 2: Design the three-layer enforcement approach

**Activities (design document, no code changes)**:

1. Confirm that Hecate's existing group structure correctly prevents LangGraph
   and Celery modules from importing adapters. If new modules have been added
   since ADR-014, propose group assignments and test them with a local Hecate
   run.

2. Design property-based checkpoint tests:
   - Identify the serialization format (is it safe to inspect with `isinstance`
     checks for adapter types?).
   - Sketch a Hypothesis strategy that generates representative checkpoints
     (e.g., planner results, evaluation outcomes).
   - Sketch property assertions: round-trip fidelity and boundary purity.

3. Design the runtime guard:
   - Identify where graph construction happens (e.g., in
     `build_generation_orchestration_graph()`).
   - Plan to introspect `node_func.__module__` for each registered node.
   - Plan error handling: what error type, message, and logging.

4. Document design in a brief `Design Rationale` section added to this plan.

**Validation**: Design review by team; no automated tests.

### Stage 3: Implement static enforcement via Hecate group review

**Activities (code changes)**:

1. Create a test in `tests/test_architecture_enforcement.py` (or a new
   `test_architecture_orchestration_enforcement.py`) that:
   - Runs Hecate on the orchestration and worker task modules with the current
     group configuration.
   - Asserts that no violations are reported.
   - Explicitly documents what the test is checking (e.g., "Celery task modules
     must not import from outbound_adapter prefixes").

2. If any modules are not yet in the correct group, update `pyproject.toml` and
   adjust group prefixes as needed.

3. Add a supplementary check (either in the test or as a separate linting rule):
   - Use Hecate's own test harness (from `tests/architecture_hecate_config.py`)
     to run a focused check on `episodic.worker.tasks*` modules only, with a
     custom rule that forbids `outbound_adapter` imports.
   - Alternatively, if adding a custom Hecate rule is complex, sketch an
     `ast-grep` pattern that detects `from episodic.canonical.storage import ...`
     within task modules, and document it as a future enhancement if the pattern
     is not adopted.

**Red-Green-Refactor**:

- **Red**: Add test that asserts no `outbound_adapter` imports in task modules.
  Run test; confirm it fails if any violations exist, or passes if the current
  code is clean.

- **Green**: If any violations are found, fix them by moving imports to
  composed-in dependencies. If no violations, the test passes; move to next
  stage.

- **Refactor**: None needed for this stage.

**Validation**: Run `make check-architecture` and `make test`; confirm no
regressions.

### Stage 4: Implement test-based checkpoint payload validation

**Activities (code changes)**:

1. Create a new test file: `tests/test_orchestration_checkpoint_payload_properties.py`.

2. Define a Hypothesis strategy that generates representative checkpoint
   payloads:
   - Use existing DTO fixtures (e.g., `PlannerResult`, `EvaluationOutcome`) as
     seeds.
   - Generate plausible payloads by calling `_planner_result_to_payload()`.

3. Write a property test that asserts:
   - **Round-trip**: `from_payload(to_payload(x)) == x` for all generated
     payloads.
   - **Boundary purity**: The payload dict contains no values that are instances
     of types whose module path is under any `outbound_adapter` prefix
     (e.g., no SQLAlchemy ORM objects, no adapter instances).

4. If the property fails on current code:
   - Document the failure in `Surprises & Discoveries`.
   - Implement the minimum change to `_checkpoint_payload.py` to make the test
     pass (e.g., strip ORM objects before serialization, or convert them to DTOs).

5. Run the property test with a reasonable example count (e.g., 100 examples).
   Profile execution time; if tests are slow, narrow the strategy or reduce
   example count and document the trade-off.

**Red-Green-Refactor**:

- **Red**: Write property test with `@pytest.mark.xfail(strict=True)` if you
  expect it to fail initially. Run test; confirm failure for the expected reason
  (if using xfail).

- **Green**: Implement changes to payload serialization (if needed) to satisfy
  the property. Run test; confirm it passes.

- **Refactor**: Clean up test code; ensure assertions are clear and
  maintainable.

**Validation**: Run `make test` with focus on the new checkpoint tests; confirm
all tests pass.

### Stage 5: Implement runtime guard at graph construction

**Activities (code changes)**:

1. In `episodic/orchestration/langgraph.py`, add a helper function that
   validates node function modules:

   ```python
   def _validate_node_module(func: Callable, node_name: str) -> None:
       """Assert that func is defined in an orchestration module."""
       module_path = func.__module__
       allowed_prefixes = ("episodic.orchestration",)
       if not any(module_path.startswith(prefix) for prefix in allowed_prefixes):
           raise ArchitectureBoundaryError(
               f"LangGraph node '{node_name}' is defined in module '{module_path}', "
               f"which is not under 'episodic.orchestration'. "
               f"Node functions must be defined in orchestration modules only. "
               f"See ADR-014 for architectural constraints."
           )
   ```

2. Call this guard in `build_generation_orchestration_graph()` whenever a node
   is registered:

   ```python
   def build_generation_orchestration_graph(...) -> StateGraph:
       graph = StateGraph(GenerationState)
       # For each node, validate before adding:
       _validate_node_module(_plan_node, "plan")
       graph.add_node("plan", _plan_node)
       # ... repeat for other nodes
       graph.set_entry_point("plan")
       graph.set_finish_point("finish")
       return graph
   ```

3. Define `ArchitectureBoundaryError` in `episodic/orchestration/_protocols.py`
   as a domain error type (not a generic `RuntimeError`).

4. Write unit tests in a new file `tests/test_orchestration_langgraph_enforcement.py`:
   - Test that the guard passes for node functions defined in
     `episodic.orchestration`.
   - Test that the guard raises `ArchitectureBoundaryError` for a mock node
     function with `__module__ = "episodic.canonical.adapters"`.
   - Test that the error message is clear and actionable.

**Red-Green-Refactor**:

- **Red**: Write tests that expect the guard to reject out-of-boundary node
  functions. Run tests; confirm they fail (the guard does not yet exist).

- **Green**: Implement the guard function and assertions. Run tests; confirm
  they pass. Run existing graph construction tests; confirm no regressions.

- **Refactor**: Clean up error messages; ensure logging is helpful if the guard
  is triggered in production.

**Validation**: Run `make test`; confirm all enforcement tests pass and
existing orchestration tests are unaffected.

### Stage 6: Update documentation and ADR

**Activities (documentation changes)**:

1. Update ADR-014 (`docs/adr/adr-014-hexagonal-architecture-enforcement.md`):
   - Add a section titled "Orchestration Code Enforcement (2.4.5)" that documents
     the three-layer approach: static (Hecate group rules), test-based
     (Hypothesis property tests), and runtime (graph construction guard).
   - Explain what each layer enforces and its limitations (e.g., static layer
     does not catch dynamic imports).
   - Reference the new test files and the `ArchitectureBoundaryError` type.

2. Update the hexagonal architecture enforcement section in
   `episodic-podcast-generation-system-design.md`:
   - Clarify that LangGraph nodes and Celery tasks are now validated at the
     function level via runtime guards, not just at the module level.
   - Document the checkpoint payload boundary discipline: payloads are validated
     via property tests to ensure they do not embed adapter types.

3. Add a section to `docs/developers-guide.md` under "Orchestration Code"
   (create if it does not exist):

   ```plaintext
   ## Writing LangGraph Node Functions

   All node functions passed to a StateGraph must be defined within the
   `episodic.orchestration` module and must only depend on domain services and
   port protocols. Direct imports of adapter classes (from
   `episodic.canonical.adapters`, etc.) are forbidden and will be detected at
   graph construction time.

   Good:
   - Node functions call `PlannerPort.plan()` and `ToolExecutorPort.execute()`.
   - Node functions are defined in `episodic/orchestration/langgraph.py` or
     submodules.

   Bad:
   - Node functions call `storage.save_episode()` directly.
   - Node functions are defined in `episodic.canonical.adapters` or other
     adapter modules.

   See ADR-014 for the full enforcement strategy.
   ```

**Validation**: Manual review of documentation updates; no automated tests.

### Stage 7: Validation gates and final verification

**Activities**:

1. Run `make check-fmt` and fix any formatting issues.
2. Run `make lint` and fix any linting errors.
3. Run `make typecheck` and fix any type errors.
4. Run `make test` and confirm all tests pass (including new checkpoint and
   guard tests).
5. Commit all changes with a clear message summarizing the enforcement layers
   added.
6. Run `coderabbit review --agent` and address any review concerns before
   merging.

**Validation gates (must all pass before completion)**:

- `make check-fmt` returns exit code 0.
- `make lint` returns exit code 0.
- `make typecheck` returns exit code 0.
- `make test` passes with at least <N> new tests for checkpoint payloads and
  LangGraph guard (to be confirmed during implementation).
- No regressions in existing tests.
- Code review feedback addressed and approved.


## Concrete steps

Detailed commands to run and expected outputs. This section will be updated as
work proceeds.

### Step 0: Understand existing enforcement (Stage 1)

```bash
# Review Hecate configuration
grep -A 100 "\[tool.hecate\]" /tmp/lody-title-agent/episodic-work/pyproject.toml

# Confirm Hecate can run
cd /tmp/lody-title-agent/episodic-work
python -m hecate check
# Expected: exit code 0 (no violations) or non-zero with ARCH001 diagnostics

# List all tests related to architecture enforcement
find tests -name "*architecture*" -type f | sort

# Understand node function structure
grep -n "def _.*_node" episodic/orchestration/langgraph.py | head -20

# Understand checkpoint serialization
head -50 episodic/orchestration/_checkpoint_payload.py
```

### Step 1: Implement static enforcement test (Stage 3)

```bash
# Create or modify test
# (Exact path and content TBD during implementation)

# Run the test
cd /tmp/lody-title-agent/episodic-work
python -m pytest tests/test_architecture_orchestration_enforcement.py -v

# Expected: Test passes if no outbound_adapter imports in task modules
```

### Step 2: Implement checkpoint property test (Stage 4)

```bash
# Create test file
touch tests/test_orchestration_checkpoint_payload_properties.py

# Write property test with Hypothesis

# Run test
cd /tmp/lody-title-agent/episodic-work
python -m pytest tests/test_orchestration_checkpoint_payload_properties.py -v

# Expected: Test passes; round-trip and boundary-purity properties hold
```

### Step 3: Implement runtime guard (Stage 5)

```bash
# Edit episodic/orchestration/langgraph.py to add guard

# Create test file
touch tests/test_orchestration_langgraph_enforcement.py

# Run enforcement test
cd /tmp/lody-title-agent/episodic-work
python -m pytest tests/test_orchestration_langgraph_enforcement.py -v

# Expected: Tests pass; guard rejects out-of-boundary node functions

# Run full orchestration tests to ensure no regressions
python -m pytest tests/test_orchestration*.py -v
# Expected: All tests pass
```

### Step 4: Validation gates (Stage 7)

```bash
cd /tmp/lody-title-agent/episodic-work

# Format
make check-fmt
# Expected: exit code 0

# Lint
make lint
# Expected: exit code 0

# Type check
make typecheck
# Expected: exit code 0

# Tests
make test
# Expected: exit code 0, all tests pass including new enforcement tests

# Code review
coderabbit review --agent
# Expected: Review completed with no critical concerns
```


## Validation and acceptance

### Code-based acceptance criteria

The implementation is complete when all of the following are true:

1. **Static enforcement**: A test in `test_architecture_enforcement.py` (or
   `test_architecture_orchestration_enforcement.py`) asserts that Hecate finds
   no violations in orchestration and task modules. The test explicitly documents
   what it checks (e.g., "no outbound_adapter imports in task modules").

2. **Test-based checkpoint validation**: A property-based test in
   `test_orchestration_checkpoint_payload_properties.py` asserts that:
   - Checkpoint payloads round-trip correctly through serialization.
   - Payloads do not contain instances of adapter types (verified via type
     inspection).
   - The test runs with at least 50 Hypothesis examples without timeout.

3. **Runtime guard**: The `_validate_node_module()` function in
   `episodic/orchestration/langgraph.py` exists and is called during graph
   construction. Unit tests in `test_orchestration_langgraph_enforcement.py`
   verify that:
   - Node functions defined in `episodic.orchestration` pass the guard.
   - Node functions with `__module__` outside `episodic.orchestration` raise
     `ArchitectureBoundaryError`.
   - Error messages are clear and actionable.

4. **Documentation**: ADR-014 is updated with a new "Orchestration Code
   Enforcement" section. `episodic-podcast-generation-system-design.md` mentions
   the three-layer approach. `docs/developers-guide.md` includes guidance for
   writing orchestration code.

5. **Quality gates**: All of the following pass:
   - `make check-fmt` (exit code 0)
   - `make lint` (exit code 0)
   - `make typecheck` (exit code 0)
   - `make test` (exit code 0; includes new tests)
   - No regressions in existing tests

### Observable behavior after implementation

After this change:

1. Running `make check-architecture` detects any unauthorized imports in
   orchestration or task modules (via Hecate).

2. Running `make test` includes execution of property-based checkpoint tests
   that validate boundary purity under serialization.

3. Starting the orchestration service (or running any code that calls
   `build_generation_orchestration_graph()`) will fail fast with a clear
   `ArchitectureBoundaryError` if a node function is accidentally defined
   outside the orchestration boundary.

4. Future developers can read ADR-014 and `developers-guide.md` to understand
   the enforcement rules and expectations for orchestration code.


## Idempotence and recovery

All steps in this plan are idempotent: re-running a stage does not cause
unintended side effects.

- **Tests**: Re-running test stages will re-run the tests; if they pass, no
  changes occur. If they fail, the failure is deterministic.
- **Code changes**: File edits are all additions or modifications to existing
  structures. There are no deletions or destructive refactors that would require
  rollback.
- **Documentation**: Edits to ADR and design docs are additive; no prior
  sections are deleted.

If a stage fails:

1. Review the failure message and any test output.
2. Fix the underlying issue (code, test assertion, or documentation).
3. Re-run the stage; the test or check will pass.
4. Proceed to the next stage.

If a tolerance threshold is breached (e.g., test complexity exceeds 300 lines),
pause and escalate to the team before continuing.


## Artifacts and notes

Key artifacts produced during this plan (to be populated as work proceeds):

- New test file: `tests/test_orchestration_checkpoint_payload_properties.py`
  (estimated 150–200 lines, including Hypothesis strategies and assertions).
- New test file: `tests/test_orchestration_langgraph_enforcement.py`
  (estimated 100–150 lines, unit tests for the guard).
- Modified file: `episodic/orchestration/langgraph.py` (add guard function and
  calls; estimated 20–40 new lines).
- Modified file: `episodic/orchestration/_protocols.py` (add
  `ArchitectureBoundaryError` type; estimated 5–10 new lines).
- Updated ADR-014 and design documents (estimated 50–100 new lines combined).


## Interfaces and dependencies

The implementation introduces one new error type and a guard function:

**New error type in `episodic/orchestration/_protocols.py`**:

```python
class ArchitectureBoundaryError(Exception):
    """Raised when orchestration code violates hexagonal architecture boundaries."""
    pass
```

**New guard function in `episodic/orchestration/langgraph.py`**:

```python
def _validate_node_module(func: Callable, node_name: str) -> None:
    """
    Assert that a node function is defined in an orchestration module.

    Raises:
        ArchitectureBoundaryError: if func.__module__ is not in the orchestration
            boundary.
    """
    # Implementation TBD
```

**Property test strategy in `tests/test_orchestration_checkpoint_payload_properties.py`**:

```python
from hypothesis import given, strategies as st
from episodic.orchestration._checkpoint_payload import (
    _planner_result_to_payload,
    _planner_result_from_payload,
)

@given(
    # Strategy TBD based on actual DTO types
    st.just(...)
)
def test_checkpoint_payload_round_trip(payload: dict) -> None:
    """Assert checkpoint payloads round-trip without data loss."""
    # Implementation TBD

def test_checkpoint_payload_boundary_purity(payload: dict) -> None:
    """Assert checkpoint payloads contain no adapter types."""
    # Implementation TBD
```

No external dependencies beyond those already in `pyproject.toml` are required.
Hypothesis is already pinned; Hecate is already pinned. The only additions are
pure Python code.

---

## Revision note (for future updates)

As work proceeds, this plan will be updated in the sections marked "To be filled
in as work proceeds" and in the `Progress` section. Key updates will note:

- When stage gates are passed or breached.
- Any surprises or discoveries that alter the scope.
- Decision log entries for design choices made during implementation.
- Final outcomes and retrospective findings upon completion.

