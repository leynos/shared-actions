# Regression Coverage for Conditional Action Dependency Manifests (3.14.5)

This ExecPlan (execution plan) is a living document. The sections `Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: DRAFT (team review in progress)


## Purpose / big picture

Add comprehensive regression test coverage for conditional action dependency manifest functionality. This feature enables GitHub Actions (or similar workflow systems) to express conditional execution (`when` clauses), iteration patterns (`foreach`), and dependency relationships between actions. After this change, the system has demonstrable confidence that:

- Actions with `when` conditions execute only when their conditions are true.
- Actions with `foreach` iterate correctly over specified ranges and propagate results.
- Dependencies between actions are correctly lowered into an intermediate representation (IR) and emitted as valid Ninja build statements.
- Fallback handling (absent-command execution without shell invocation) works correctly.
- Complementary test runner branches (nextest vs. legacy) correctly select exactly one action per scenario.

Success is observable by running `make test` and seeing all regression tests pass, with coverage metrics showing the conditional action dependency pathway is exercised end-to-end.


## Constraints

Hard invariants that must hold throughout implementation:

- Existing test suite must continue to pass without modification. This is purely an additive task.
- The conditional action feature API and IR representation must remain stable. Any changes to public signatures require escalation.
- All tests must run on the CI pipeline without requiring special permissions or external services.
- The test suite must complete within the project's existing timeout budgets (no test should exceed 30 seconds).
- No mock of external services or environment dependencies; tests must work with real or in-memory substitutes.
- Tests must use only `rstest`, `rstest-bdd`, `insta`, `proptest`, and `kani` — no additional test frameworks.
- Documentation referenced in tests (via `ortho_config`) must be accurate and maintainable.


## Tolerances (exception triggers)

- Scope: if implementation requires changes to more than 15 files, stop and escalate.
- Test count: if adding more than 50 new test cases, stop and escalate.
- LOC: if net code addition (tests + implementation) exceeds 3000 lines, stop and escalate.
- Duration: if any test milestone takes more than 4 hours, stop and escalate.
- Build time: if running the full test suite takes more than 5 minutes, stop and escalate.
- Ambiguity: if it is unclear how to test a conditional action scenario or what the expected behaviour should be, stop and present options.


## Risks

- Risk: Conditional action semantics may not be fully documented. Understanding what `when` and `foreach` should do may require reading implementation code rather than design docs.
  Severity: high (MITIGATED)
  Likelihood: medium → low
  Mitigation: ✓ COMPLETED — firecrawl research (2026-06-15) confirmed:
    - GitHub Actions conditionals are deterministic and well-specified (context variables, boolean logic, short-circuit evaluation)
    - Semantics are straightforward to test (fixed inputs → deterministic outputs)
    - Prior art from Ninja, Bazel, CMake provides clear testing patterns
  Next step: Use Stage A to locate and review the feature implementation in the codebase; semantics are now well-grounded in prior art.

- Risk: The IR representation and Ninja emission may have subtle correctness requirements (e.g., correct escaping of dependency names, ordering constraints) that are easy to miss in unit tests.
  Severity: medium
  Likelihood: high
  Mitigation: Use snapshot tests (insta) to capture the Ninja output and ensure diffs are explicit. Use bounded model checking (kani) to verify invariants over the IR.

- Risk: Complementary test runner branches (nextest vs. legacy) may have subtle differences in execution ordering or output format that cause only one branch to be exercised in the test suite.
  Severity: medium
  Likelihood: medium
  Mitigation: Design tests parametrically so the same test logic runs against both branches. Use property-based testing (proptest) to ensure behaviour is consistent across branches.

- Risk: The absent-command fallback path may not be reachable in normal test scenarios (e.g., because shell is available). Tests may pass without actually exercising the fallback.
  Severity: medium
  Likelihood: low
  Mitigation: Inject test doubles or feature flags to force the fallback path without modifying production code. Verify via code coverage.

- Risk: The `ortho_config` layered configuration system may be unfamiliar to future maintainers. Documentation and test ergonomics are critical.
  Severity: low
  Likelihood: high
  Mitigation: Structure tests with clear setup comments explaining the config layers. Add docstring examples in the test modules.


## Progress

Use a list with checkboxes to summarise granular steps. Every stopping point must be documented here.

- [ ] Stage A: Research and specification (understand feature semantics, research prior art).
- [ ] Stage B: Specification tests and BDD scenarios (red tests that fail before implementation).
- [ ] Stage C: Unit test implementation (rstest-based tests for conditional logic).
- [ ] Stage D: Integration and behavioural tests (rstest-bdd tests for end-to-end scenarios).
- [ ] Stage E: Property-based and model-checking tests (proptest and kani where applicable).
- [ ] Stage F: Documentation and cleanup (update docs, ensure all gates pass).
- [ ] Stage G: CodeRabbit review and sign-off.


## Surprises & discoveries

To be populated as work proceeds.


## Decision log

- Decision: Use firecrawl to research GitHub Actions conditional execution and dependency semantics before designing tests, rather than guessing from the codebase.
  Rationale: GitHub Actions is a mature, documented system. Prior art exists and will inform our test strategy.
  Research findings (2026-06-15):
    - GitHub Actions conditions are deterministic: evaluated with JavaScript-like boolean logic, context variable substitution, short-circuit evaluation
    - Available contexts: `github` (ref, sha, event_name, actor), `env`, `secrets`, `matrix`, `needs` (outputs), `vars`
    - Evaluation rules are straightforward: strings truthy unless empty, null/undefined falsy, numeric coercion in comparisons
    - Test implication: Snapshot testing of "would this condition fire?" is reliable given fixed inputs
  Date/Author: 2026-06-15 (agent team research phase).

- Decision: Use snapshot tests (insta) to capture Ninja build statement output rather than parsing and asserting individual fields.
  Rationale: Ninja output format is stable and complex; snapshot tests make diffs explicit and catch unintended changes easily.
  Research findings (2026-06-15):
    - Ninja uses DAG-based representation: rules (transformations), edges (dependencies)
    - .ninja file syntax is deterministic and validation-friendly
    - Mature systems (Ninja, Bazel, CMake) all use golden-file snapshots for build output
    - Test implication: Combine snapshot tests (catch unexpected changes) with property tests (catch systematic bugs like introduced cycles)
  Date/Author: 2026-06-15 (agent team research phase).

- Decision: Use parametrised tests (rstest `#[case]` or `#[values]`) to exercise both nextest and legacy test runner branches in the same test logic.
  Rationale: Reduces code duplication and ensures both branches are tested identically.
  Test strategy implication: Parametrised tests enable both branches to be validated without duplication; clear test names describing the condition being tested (e.g., `test_conditional_step_with_matrix_context`) improve maintainability.
  Date/Author: 2026-06-15 (team design alignment).

- Decision: Focus test validation on parse-time checks (DAG validity, cycle detection, missing targets) before snapshot matching.
  Rationale: Parse-time validation is cheap and catches most errors. Research findings show all mature build systems validate at parse time.
  Validation checklist: No cycles (verified via topological sort), all explicit deps declared as targets, no orphaned targets, proper escaping of special characters in Ninja output.
  Date/Author: 2026-06-15 (agent team research synthesis).


## Outcomes & retrospective

To be filled after completion.


## Context and orientation

This project is a GitHub Actions validation suite built in Rust. The codebase includes:

- `rust-toy-app/` — a simple test fixture application with CLI and library components.
- `rust-toy-app/tests/` — integration and behavioural tests using rstest, rstest-bdd, and cucumber.rs.
- `docs/execplans/` — living documentation for complex changes.
- `docs/developers-guide.md` — internal architecture and testing conventions.
- `docs/ortho-config-users-guide.md` — guide to layered configuration (referenced in tests).
- `Makefile` — tool resolution and test/lint/format targets.

The conditional action dependency manifest feature is a mechanism for expressing:

1. **Conditional Execution (`when`)**: An action executes only if its condition evaluates to true. Conditions are expressions over action outputs, environment variables, or literals.
2. **Iteration (`foreach`)**: An action may iterate over a set (array, range, or map) and spawn multiple concrete task instances.
3. **Dependency Specification (`deps`)**: Actions can declare dependencies on other actions or on external artifacts. These are lowered into an IR and later emitted as Ninja build statements.
4. **Fallback Handling (absent-command)**: If a command is not found, a fallback behaviour (often a no-op or default) is invoked without invoking the shell.

The IR is an intermediate representation that captures the structure of conditional actions and dependencies. The Ninja backend emits this IR as a `.ninja` file with proper edge and rule definitions.

Key files to understand (to be identified during Stage A):

- [Module/file path for conditional action definitions] — defines the conditional action AST.
- [Module/file path for IR lowering] — transforms conditional actions into IR.
- [Module/file path for Ninja emission] — generates Ninja build statements from IR.
- [Module/file path for test runner logic] — handles nextest vs. legacy branch selection.

(These paths will be populated during the planning phase using `leta` code navigation.)


## Plan of work

### Stage A: Research and Specification

The goal of Stage A is to understand the feature deeply and document its semantics so that tests are unambiguous.

**Actions**:

1. Use `firecrawl` to research open-source prior art:
   - GitHub Actions conditional execution documentation and examples.
   - Ninja build system dependency model and syntax.
   - Common patterns for `when` and `foreach` in workflow systems.
   - Document findings in a brief research summary to be added to the Decision Log.

2. Use `leta` to locate and review key modules:
   - Find the module defining conditional action structs/enums (AST).
   - Find the module implementing IR lowering.
   - Find the module implementing Ninja code emission.
   - Find the test runner branch selection logic (nextest vs. legacy).
   - Find or create the absent-command fallback implementation.

3. Document the feature semantics as a prose specification:
   - What does a `when` condition do? When is it evaluated? What data is available?
   - What does a `foreach` do? What are valid iteration sources?
   - What is the IR? How does it relate to Ninja?
   - What does absent-command fallback mean? When is it triggered?

4. Identify test scenarios:
   - List the "happy path" scenarios for each feature (when, foreach, deps, absent-command).
   - List edge cases (empty foreach, falsy when conditions, circular dependencies, etc.).
   - List error scenarios (invalid syntax, missing dependencies, etc.).

**Validation**: Stage A is complete when the specification and test scenarios are documented in the Decision Log and there are no ambiguities about what should be tested.


### Stage B: Specification Tests and BDD Scenarios

The goal of Stage B is to write failing tests (red phase) that specify the expected behaviour before implementation.

**Actions**:

1. Create or update test module `rust-toy-app/tests/conditional_actions.rs`:
   - Import rstest, insta, and proptest.
   - Add a module for test helpers (e.g., `build_action`, `assert_ninja_contains`).
   - Add placeholder BDD feature scenarios (using comments for now, or stub rstest-bdd if available).

2. Write unit test stubs for conditional execution:
   - `test_when_condition_true_action_executes()` — verify an action with `when: true` is included in IR.
   - `test_when_condition_false_action_skipped()` — verify an action with `when: false` is excluded from IR.
   - `test_when_condition_with_variable_reference()` — verify conditional evaluation with variable substitution.
   - `test_when_condition_complex_expression()` — verify AND/OR logic in conditions.

3. Write unit test stubs for iteration:
   - `test_foreach_over_array()` — verify an action with `foreach: ["a", "b"]` spawns 2 task instances.
   - `test_foreach_over_range()` — verify `foreach: range(1, 3)` spawns 2 instances.
   - `test_foreach_with_output_propagation()` — verify outputs from iterated tasks are collected.
   - `test_foreach_empty_set_no_tasks()` — verify empty foreach results in no tasks.

4. Write unit test stubs for dependency lowering:
   - `test_deps_simple_chain()` — verify `action_b` depends on `action_a` produces `"action_a -> action_b"` in Ninja.
   - `test_deps_multiple_dependencies()` — verify an action depending on multiple others lists them all.
   - `test_deps_transitive_closure()` — verify the IR correctly computes transitive closure.
   - `test_deps_circular_dependency_error()` — verify circular dependencies are detected and rejected.

5. Write unit test stubs for test runner branches:
   - `test_nextest_runner_selected_when_available()` — verify nextest is preferred when available.
   - `test_legacy_runner_fallback()` — verify legacy runner is used when nextest is not.
   - `test_both_branches_select_one_action()` — verify exactly one action is selected in each case.

6. Write unit test stubs for absent-command fallback:
   - `test_absent_command_fallback_no_shell_invocation()` — verify fallback is invoked without shell.
   - `test_command_present_direct_invocation()` — verify commands on PATH are invoked directly.

7. Run `make test` and verify all new tests fail with the expected reason (red phase):
   - Tests should fail because the feature is not yet implemented or the assertions are unsatisfiable.

**Validation**: Stage B is complete when:
- All new tests fail for the expected reason (e.g., "module not found" or "assertion failed").
- No tests are marked as `#[ignore]` or skipped.
- The test code is clear enough that a future maintainer can understand the expected behaviour.


### Stage C: Unit Test Implementation

The goal of Stage C is to implement the feature logic necessary to make unit tests pass (green phase).

**Actions**:

1. Implement or review the conditional action data structures:
   - Ensure `Condition`, `Action`, `Foreach` structs are defined with clear fields.
   - Ensure types are well-documented (derive Debug, Clone where appropriate).

2. Implement or review the IR representation:
   - Define IR types for conditions, actions, and dependencies.
   - Implement lowering logic from `Action` to IR.

3. Implement or review Ninja emission:
   - Implement logic to emit IR as valid Ninja build statements.
   - Use snapshot tests (insta) to capture output.

4. Implement or review test runner branch selection:
   - Ensure logic correctly selects nextest or legacy based on availability.
   - Ensure selection is deterministic and testable.

5. Implement or review absent-command fallback:
   - Ensure fallback is invoked when commands are not found.
   - Ensure shell is not invoked during fallback.

6. Run focused tests and verify green phase:
   - `cargo test --lib conditional_actions` should pass.
   - Snapshot diffs should be reviewed and approved (committed to repo).

7. Run full test suite and verify no regressions:
   - `make test` should pass.
   - `make lint` and `make typecheck` should pass.

**Validation**: Stage C is complete when:
- All unit tests pass.
- No new clippy warnings are introduced.
- Snapshot tests are committed and approved.


### Stage D: Integration and Behavioural Tests

The goal of Stage D is to add rstest-bdd tests that exercise the feature end-to-end.

**Actions**:

1. Add rstest-bdd scenarios (or cucumber-rs if already in use):
   - Create `rust-toy-app/tests/features/conditional_actions.feature` with BDD scenarios.
   - Scenarios should cover:
     - A when-condition gates action execution (happy path).
     - A foreach iterates and produces multiple results.
     - A dependency chain produces correct Ninja edges.
     - A missing command triggers the fallback without shell invocation.
   - Each scenario should use Given-When-Then structure.

2. Implement BDD step definitions:
   - `Given an action with condition <expr>` — build an action with the given condition.
   - `When we lower the action to IR` — call the lowering function.
   - `Then the action should be [included|excluded]` — assert IR state.
   - Similar steps for foreach, deps, and absent-command.

3. Add behavioural test helpers:
   - Function to create a minimal action manifest.
   - Function to lower and emit Ninja.
   - Function to parse Ninja output and assert properties.

4. Run BDD tests and verify green phase:
   - `cargo test --test bdd` (or equivalent for rstest-bdd) should pass.

5. Run full test suite and verify no regressions:
   - `make test` should pass.

**Validation**: Stage D is complete when:
- All BDD scenarios pass.
- Scenarios cover the happy paths, edge cases, and error scenarios identified in Stage A.
- No regressions in existing tests.


### Stage E: Property-Based and Model-Checking Tests

The goal of Stage E is to add advanced testing for complex invariants using proptest and kani.

**Actions**:

1. Add proptest-based tests:
   - Generate random conditional action manifests and verify the IR is consistent.
   - Example: generate random when-conditions and verify IR inclusion/exclusion is deterministic.
   - Example: generate random foreach sets and verify task count equals set size.
   - Example: generate random dependency graphs and verify no unintended cycles are introduced.

2. Add kani bounded model checking (if applicable):
   - Use kani to verify IR lowering is sound (no unintended state transitions).
   - Use kani to verify Ninja emission is valid (e.g., no invalid edge definitions).
   - Use kani to verify the absent-command fallback does not invoke shell.

3. Run proptest and kani tests:
   - `cargo test --lib conditional_actions_proptest` should pass.
   - `cargo kani --harness conditional_actions_model` should verify all assertions (if harness exists).

4. Run full test suite and verify no regressions:
   - `make test` should pass.

**Validation**: Stage E is complete when:
- Property-based tests pass and cover the key invariants.
- Model-checking tests (if applicable) verify correctness.
- No regressions.


### Stage F: Documentation and Cleanup

The goal of Stage F is to ensure the test suite is maintainable and all quality gates pass.

**Actions**:

1. Review and update test module documentation:
   - Add module-level docstring explaining the feature and test structure.
   - Add docstrings to complex test functions.
   - Ensure examples are clear and correct.

2. Update `docs/developers-guide.md`:
   - Add section on testing conditional actions.
   - Document the IR representation and Ninja emission.
   - Explain how the test runner branch selection works.

3. Update `docs/users-guide.md` (if a user guide exists):
   - Document the `when`, `foreach`, and `deps` syntax.
   - Provide examples of conditional action manifests.

4. Verify `ortho_config` integration:
   - Ensure test setup uses `ortho_config` for layered configuration.
   - Document how to configure conditional action behaviour in tests.

5. Run all quality gates:
   - `make check-fmt` — verify formatting.
   - `make typecheck` — verify type safety.
   - `make lint` — verify lint rules.
   - `make test` — verify all tests pass.

6. Commit all changes:
   - Commit test code, documentation, and snapshots.
   - Ensure commit message references this execplan.

**Validation**: Stage F is complete when:
- All quality gates pass.
- Documentation is accurate and helpful.
- Code is clean and ready for review.


### Stage G: CodeRabbit Review and Sign-Off

The goal of Stage G is to obtain independent review and resolve any concerns before completion.

**Actions**:

1. Request CodeRabbit review:
   - Run `coderabbit review --agent` on the full diff.
   - Document findings and resolutions in the Decision Log.

2. Address any review concerns:
   - If performance concerns are raised, add benchmarks or optimize.
   - If test coverage is insufficient, add additional tests.
   - If code quality issues are found, refactor.

3. Re-run full test suite and gates:
   - `make test` should pass.
   - `make lint`, `make typecheck`, `make check-fmt` should pass.

4. Mark the execplan as COMPLETE and the roadmap item as done.

**Validation**: Stage G is complete when:
- CodeRabbit review has no unresolved concerns.
- All tests and gates pass.
- The roadmap item (3.14.5) is marked as done.


## Concrete steps

Steps and commands will be documented as work proceeds and milestones are reached.

### Running the Test Suite

To run the conditional action regression tests during development:

```bash
# Run all tests
make test

# Run only conditional action tests
cargo test --lib conditional_actions

# Run BDD tests
cargo test --test bdd -- conditional_actions

# Run linting and formatting checks
make lint
make check-fmt
make typecheck
```

### Expected Test Output

After Stage C (unit tests), expect output similar to:

```
running 12 tests

test conditional_actions::test_when_condition_true_action_executes ... ok
test conditional_actions::test_when_condition_false_action_skipped ... ok
test conditional_actions::test_foreach_over_array ... ok
test conditional_actions::test_foreach_over_range ... ok
test conditional_actions::test_deps_simple_chain ... ok
test conditional_actions::test_ninja_snapshot_comparison ... ok
test conditional_actions::test_absent_command_fallback_no_shell ... ok

test result: ok. 12 passed; 0 failed; 0 ignored

```

After Stage D (BDD tests), expect additional output:

```
running 1 test
test conditional_actions_bdd ... ok
  scenario: a when-condition gates action execution ... ok
  scenario: a foreach iterates and produces results ... ok
  scenario: a dependency chain produces correct ninja edges ... ok

```

### Snapshot Management

Snapshot tests using `insta` will be created during implementation. To review and approve snapshots:

```bash
# Review snapshot diffs
cargo insta review

# Accept new snapshots
cargo insta accept
```

Snapshots should be committed alongside test code.


## Validation and acceptance

### Test-Driven Development: Red-Green-Refactor

1. **Red Phase** (Stage B):
   - Write failing tests with clear assertions.
   - Command: `make test 2>&1 | tee /tmp/test-red.log`
   - Expected: Tests fail with clear error messages indicating the feature is missing or incorrect.

2. **Green Phase** (Stages C-E):
   - Implement the feature to make tests pass.
   - Command: `make test 2>&1 | tee /tmp/test-green.log`
   - Expected: All tests pass.

3. **Refactor Phase** (Stage F):
   - Clean up code, improve tests, ensure documentation is clear.
   - Command: `make test && make lint && make typecheck && make check-fmt`
   - Expected: All gates pass with no warnings.

### Quality Criteria

- **Tests**: All new tests pass, including unit, BDD, property-based, and model-checking tests.
- **Lint**: `make lint` passes with no new warnings or errors.
- **Type Safety**: `make typecheck` passes with no type errors.
- **Formatting**: `make check-fmt` passes; code is formatted correctly.
- **Documentation**: Docstrings and module documentation are clear and complete.
- **Coverage**: Code coverage for conditional action functionality is above 85% (verify via `cargo tarpaulin` or project coverage tool).
- **Performance**: No new tests add more than 1 second to the overall test suite.

### Acceptance Criteria

Success is demonstrated by:

1. Running `make test` and seeing all conditional action tests pass.
2. Running `make lint && make typecheck && make check-fmt` with no errors.
3. Running `cargo tarpaulin --exclude-files tests/` and verifying conditional action modules have > 85% coverage.
4. Opening the generated Ninja output and visually verifying the structure is correct (edge definitions, rule definitions, proper escaping).
5. Manually testing a complex conditional action manifest (with when, foreach, and deps) and observing the correct task ordering and execution.


## Idempotence and recovery

All test commands are idempotent. Re-running tests does not change the outcome (assuming no code changes). If a test fails intermittently:

1. Run `make test` again to confirm the failure is reproducible.
2. Check for timing-dependent code or flaky randomness (e.g., in proptest). Add fixed seeds if needed.
3. If a test hangs, check for deadlocks or infinite loops in the feature implementation.
4. If snapshot tests diverge, review the diff with `cargo insta review` and ensure the change is intentional.

If a full rebuild is needed (e.g., after a clean), run:

```bash
cargo clean && make test
```

This will rebuild all dependencies and run the full test suite.


## Artifacts and notes

### Key Modules (To be populated during Stage A with `leta`)

- Conditional action definitions: `[module path]`
- IR lowering: `[module path]`
- Ninja emission: `[module path]`
- Test runner selection: `[module path]`

### Test File Structure

```
rust-toy-app/tests/
├── conditional_actions.rs      # Unit and integration tests
├── features/
│   └── conditional_actions.feature  # BDD scenarios (if using cucumber)
└── test_helpers/
    └── conditional_actions.rs  # Shared test helpers
```

### Snapshot Snapshots Location

```
rust-toy-app/tests/snapshots/
├── conditional_actions__test_ninja_snapshot_comparison.snap
├── conditional_actions__test_foreach_output_snapshot.snap
└── ...
```


## Interfaces and dependencies

### Feature Interfaces

Based on the task description, the following interfaces are expected to exist:

```rust
// Conditional action type (to be discovered via `leta`)
pub struct ConditionalAction {
    pub name: String,
    pub condition: Option<Condition>,
    pub foreach: Option<ForeachSpec>,
    pub deps: Vec<String>,
    pub command: Option<String>,
}

// Condition type
pub enum Condition {
    Literal(bool),
    Variable(String),
    And(Box<Condition>, Box<Condition>),
    Or(Box<Condition>, Box<Condition>),
    // ... other variants
}

// Foreach specification
pub struct ForeachSpec {
    pub over: IterationSource,
}

pub enum IterationSource {
    Array(Vec<String>),
    Range(u32, u32),
    // ... other sources
}

// IR type
pub struct ActionIR {
    pub tasks: Vec<Task>,
    pub edges: Vec<Edge>,
}

pub struct Task {
    pub name: String,
    pub command: Option<String>,
}

pub struct Edge {
    pub from: String,
    pub to: String,
}

// Ninja emission
pub fn emit_ninja(ir: &ActionIR) -> String;

// Test runner selection
pub fn select_test_runner() -> TestRunner;

pub enum TestRunner {
    Nextest,
    Legacy,
}
```

### Testing Libraries

- `rstest` — parametrised unit tests and fixtures.
- `rstest-bdd` (or `cucumber-rs`) — behaviour-driven tests.
- `insta` — snapshot testing for Ninja output.
- `proptest` — property-based testing.
- `kani` — bounded model checking (if used).
- `googletest` assertions (if available in the project) — for clear test failures.
- `pretty_assertions` — for better diff output.

### Configuration

- `ortho_config` — layered configuration for test setup. Document how to configure conditional action behaviour in tests.
- Makefile targets: `make test`, `make lint`, `make typecheck`, `make check-fmt`.


## Related Documentation

- `docs/developers-guide.md` — internal architecture (to be updated with conditional actions section).
- `docs/users-guide.md` — user-facing documentation (if exists, to be updated with when/foreach/deps syntax).
- `docs/ortho-config-users-guide.md` — layered configuration guidance.
- `docs/rstest-bdd-users-guide.md` — BDD testing patterns (if exists).
- `docs/rust-testing-with-rstest-fixtures.md` — rstest fixture patterns.
- `docs/reliable-testing-in-rust-via-dependency-injection.md` — dependency injection in tests.


## Reference Links

- GitHub Actions Conditional Execution: https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#jobsjob_idif
- Ninja Build System: https://ninja-build.org/
- rstest Documentation: https://docs.rs/rstest/latest/rstest/
- insta Snapshot Testing: https://insta.rs/
- proptest Documentation: https://docs.rs/proptest/latest/proptest/
- Kani Bounded Model Checker: https://model-checking.github.io/kani/
