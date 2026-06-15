# Conditional Action Dependency Manifests (3.14.5)

This ExecPlan is a living document. Sections `Constraints`, `Tolerances`,
`Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must stay current as work proceeds.

Status: DRAFT (team review in progress)

## Purpose / big picture

Add comprehensive regression test coverage for conditional action
dependency manifest functionality. This feature enables GitHub Actions
(or similar workflow systems) to express conditional execution (`when`
clauses), iteration patterns (`foreach`), and dependency relationships
between actions. After this change, the system has demonstrable confidence
that:

- Actions with `when` conditions execute only when conditions are true.
- Actions with `foreach` iterate correctly and propagate results.
- Dependencies are correctly lowered to IR and emit valid Ninja build
  statements.
- Fallback handling (absent-command) works without shell invocation.
- Test runner branches (nextest vs. legacy) select exactly one action.

Success is observable by running `make test` with all regression tests
passing and coverage metrics showing the conditional action dependency
pathway is exercised end-to-end.

## Constraints

Hard invariants that must hold throughout implementation:

- Existing test suite must pass without modification.
- Conditional action feature API and IR representation must remain stable.
- All tests must run on CI without requiring special permissions.
- Test suite must complete within project timeout budgets (max 30 seconds
  per test).
- No mocking of external services; use real or in-memory substitutes.
- Tests must use only `rstest`, `rstest-bdd`, `insta`, `proptest`, and
  `kani`.
- Documentation referenced in tests must be accurate and maintainable.

## Tolerances (exception triggers)

- Scope: if changes exceed 15 files, escalate.
- Test count: if adding more than 50 test cases, escalate.
- LOC: if net code addition exceeds 3000 lines, escalate.
- Duration: if any milestone takes more than 4 hours, escalate.
- Build time: if full test suite exceeds 5 minutes, escalate.
- Ambiguity: if unclear how to test a scenario, escalate.

## Risks

- Risk: Conditional action semantics may not be fully documented.
  Severity: high (MITIGATED by research)
  Likelihood: medium → low
  Mitigation: ✓ firecrawl research (2026-06-15) confirmed GitHub Actions
  conditionals are deterministic and well-specified.

- Risk: IR representation and Ninja emission have subtle correctness
  requirements.
  Severity: medium
  Likelihood: high
  Mitigation: Use snapshot tests (insta) and bounded model checking
  (kani).

- Risk: Test runner branches may have subtle execution differences.
  Severity: medium
  Likelihood: medium
  Mitigation: Use parametrised tests; property-based testing for
  consistency.

- Risk: Absent-command fallback may not be reachable in tests.
  Severity: medium
  Likelihood: low
  Mitigation: Inject test doubles; verify via code coverage.

- Risk: `ortho_config` system may be unfamiliar to maintainers.
  Severity: low
  Likelihood: high
  Mitigation: Clear comments and docstring examples in test modules.

## Progress

- [ ] Stage A: Research and specification
- [ ] Stage B: Specification tests and BDD scenarios
- [ ] Stage C: Unit test implementation
- [ ] Stage D: Integration and behavioural tests
- [ ] Stage E: Property-based and model-checking tests
- [ ] Stage F: Documentation and cleanup
- [ ] Stage G: CodeRabbit review and sign-off

## Surprises & discoveries

To be populated as work proceeds.

## Decision log

- **Decision**: Use firecrawl to research GitHub Actions conditional
  execution before designing tests.
  **Rationale**: GitHub Actions is mature and documented.
  **Research** (2026-06-15): Conditions use JavaScript-like boolean logic
  with context variable substitution. Evaluation is deterministic:
  strings truthy unless empty, null/undefined falsy, numeric coercion in
  comparisons. Snapshot testing is reliable.
  **Date**: 2026-06-15.

- **Decision**: Use snapshot tests (insta) for Ninja build statement
  output.
  **Rationale**: Output format is stable but complex; snapshots make diffs
  explicit.
  **Research** (2026-06-15): Ninja uses DAG-based representation. .ninja
  syntax is deterministic. Mature systems (Ninja, Bazel, CMake) use
  golden-file snapshots. Combine snapshot tests with property tests.
  **Date**: 2026-06-15.

- **Decision**: Use parametrised tests (rstest) for nextest and legacy
  branches.
  **Rationale**: Reduces duplication; ensures both branches tested
  identically.
  **Date**: 2026-06-15.

- **Decision**: Focus validation on parse-time checks before snapshot
  matching.
  **Rationale**: Parse-time validation is cheap and catches most errors.
  **Validation**: No cycles, all explicit deps declared, no orphaned
  targets, proper escaping in Ninja output.
  **Date**: 2026-06-15.

## Outcomes & retrospective

To be filled after completion.

## Context and orientation

This project is a GitHub Actions validation suite built in Rust. Key
components:

- `rust-toy-app/` — test fixture application
- `rust-toy-app/tests/` — integration and BDD tests
- `docs/execplans/` — living documentation
- `docs/developers-guide.md` — internal architecture
- `docs/ortho-config-users-guide.md` — layered configuration
- `Makefile` — tool resolution and test targets

The conditional action dependency manifest feature enables:

1. **Conditional Execution (`when`)**: Action executes only if condition
   evaluates to true.
2. **Iteration (`foreach`)**: Action iterates over set and spawns multiple
   task instances.
3. **Dependency Specification (`deps`)**: Declare dependencies between
   actions, lowered to IR and emitted as Ninja.
4. **Fallback Handling (absent-command)**: Fallback invoked without shell
   when command not found.

The IR captures conditional action structure and dependencies. Ninja
backend emits IR as `.ninja` file with proper edges and rules.

Key files to identify during Stage A using `leta`:

- Conditional action definitions (AST)
- IR lowering logic
- Ninja code emission
- Test runner branch selection

## Plan of work

### Stage A: Research and Specification

Understand feature deeply and document semantics.

**Actions**:

1. Use `firecrawl` for prior art research:
   - GitHub Actions conditional execution
   - Ninja build system dependency model
   - Common `when` and `foreach` patterns

2. Use `leta` to locate key modules:
   - Conditional action structs/enums
   - IR lowering implementation
   - Ninja code emission
   - Test runner branch selection

3. Document feature semantics:
   - When conditions: evaluation, available data
   - Foreach behavior: iteration sources
   - IR structure and Ninja relationship
   - Absent-command fallback trigger

4. Identify test scenarios:
   - Happy paths for each feature
   - Edge cases (empty foreach, falsy conditions, cycles)
   - Error scenarios (invalid syntax, missing deps)

**Validation**: Specification and test scenarios documented; no ambiguities.

### Stage B: Specification Tests

Write failing tests (red phase) before implementation.

**Actions**:

1. Create `rust-toy-app/tests/conditional_actions.rs`
2. Write unit test stubs for conditional execution
3. Write unit test stubs for iteration
4. Write unit test stubs for dependency lowering
5. Write unit test stubs for test runner branches
6. Write unit test stubs for absent-command fallback
7. Run `make test` and verify all new tests fail

**Validation**: All tests fail for expected reasons; code is clear.

### Stage C: Unit Test Implementation

Implement feature logic to make unit tests pass (green phase).

**Actions**:

1. Implement conditional action data structures
2. Implement IR representation
3. Implement Ninja emission
4. Implement test runner branch selection
5. Implement absent-command fallback
6. Run focused tests and verify green phase
7. Run full test suite and verify no regressions

**Validation**: All unit tests pass; no new warnings; snapshots approved.

### Stage D: Integration and Behavioural Tests

Add rstest-bdd tests for end-to-end scenarios.

**Actions**:

1. Create BDD scenarios covering when-conditions, foreach, deps, fallback
2. Implement BDD step definitions
3. Add behavioural test helpers
4. Run BDD tests and verify pass
5. Run full suite and verify no regressions

**Validation**: All BDD scenarios pass; good coverage; no regressions.

### Stage E: Property-Based and Model-Checking Tests

Add advanced testing for complex invariants.

**Actions**:

1. Add proptest-based tests for invariants
2. Add kani bounded model checking (if applicable)
3. Run proptest and kani tests
4. Run full suite and verify no regressions

**Validation**: Property tests pass; model-checking verified; no
regressions.

### Stage F: Documentation and Cleanup

Ensure test suite is maintainable; pass all quality gates.

**Actions**:

1. Review and update test module documentation
2. Update `docs/developers-guide.md`
3. Update `docs/users-guide.md` (if exists)
4. Verify `ortho_config` integration
5. Run all quality gates
6. Commit all changes

**Validation**: All gates pass; documentation helpful; code ready for
review.

### Stage G: CodeRabbit Review

Obtain independent review before completion.

**Actions**:

1. Request CodeRabbit review
2. Address review concerns
3. Re-run full test suite and gates
4. Mark execplan COMPLETE

**Validation**: No unresolved concerns; all tests pass; roadmap marked
done.

## Concrete steps

Steps and commands will be documented as work proceeds.

### Running the Test Suite

```bash
make test
cargo test --lib conditional_actions
cargo test --test bdd -- conditional_actions
make lint
make check-fmt
make typecheck
```

### Expected Test Output

After Stage C (unit tests):

```plaintext
running 12 tests
test conditional_actions::test_when_condition_true ... ok
test conditional_actions::test_when_condition_false ... ok
test conditional_actions::test_foreach_over_array ... ok
test conditional_actions::test_foreach_over_range ... ok
test conditional_actions::test_deps_simple_chain ... ok
test conditional_actions::test_ninja_snapshot ... ok
test conditional_actions::test_absent_command_fallback ... ok

test result: ok. 12 passed; 0 failed
```

After Stage D (BDD tests):

```plaintext
running 1 test
test conditional_actions_bdd ... ok
  scenario: when-condition gates execution ... ok
  scenario: foreach iterates and produces results ... ok
  scenario: dependency chain produces ninja edges ... ok
```

### Snapshot Management

```bash
cargo insta review
cargo insta accept
```

Snapshots should be committed alongside test code.

## Validation and acceptance

### Test-Driven Development: Red-Green-Refactor

1. **Red Phase** (Stage B): Write failing tests; verify they fail for the
   expected reason.
2. **Green Phase** (Stages C-E): Implement feature to make tests pass.
3. **Refactor Phase** (Stage F): Clean up, improve, ensure documentation.

### Quality Criteria

- All new tests pass (unit, BDD, property-based, model-checking)
- `make lint` passes with no new warnings
- `make typecheck` passes with no type errors
- `make check-fmt` passes
- Docstrings and module documentation are clear
- Code coverage > 85%
- No test adds more than 1 second to total runtime

### Acceptance Criteria

Success is demonstrated by:

1. `make test` passes with all conditional action tests
2. `make lint && make typecheck && make check-fmt` passes
3. Coverage > 85% for conditional action modules
4. Generated Ninja output is valid
5. Manual testing with complex manifests works correctly

## Idempotence and recovery

All test commands are idempotent. If tests fail intermittently:

1. Re-run `make test` to confirm reproducibility
2. Check for timing-dependent code or flaky randomness
3. Review snapshot diffs with `cargo insta review`

If full rebuild needed:

```bash
cargo clean && make test
```

## Artifacts and notes

### Key Modules (To be identified during Stage A)

- Conditional action definitions: `[module path]`
- IR lowering: `[module path]`
- Ninja emission: `[module path]`
- Test runner selection: `[module path]`

### Test File Structure

```plaintext
rust-toy-app/tests/
├── conditional_actions.rs
├── features/
│   └── conditional_actions.feature
└── test_helpers/
    └── conditional_actions.rs
```

### Snapshot Location

```plaintext
rust-toy-app/tests/snapshots/
├── conditional_actions__test_ninja_snapshot.snap
├── conditional_actions__test_foreach_output.snap
└── ...
```

## Interfaces and dependencies

### Feature Interfaces

```rust
pub struct ConditionalAction {
    pub name: String,
    pub condition: Option<Condition>,
    pub foreach: Option<ForeachSpec>,
    pub deps: Vec<String>,
    pub command: Option<String>,
}

pub enum Condition {
    Literal(bool),
    Variable(String),
    And(Box<Condition>, Box<Condition>),
    Or(Box<Condition>, Box<Condition>),
}

pub struct ForeachSpec {
    pub over: IterationSource,
}

pub enum IterationSource {
    Array(Vec<String>),
    Range(u32, u32),
}

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

pub fn emit_ninja(ir: &ActionIR) -> String;

pub fn select_test_runner() -> TestRunner;

pub enum TestRunner {
    Nextest,
    Legacy,
}
```

### Testing Libraries

- `rstest` — parametrised tests and fixtures
- `rstest-bdd` (or `cucumber-rs`) — behaviour-driven tests
- `insta` — snapshot testing
- `proptest` — property-based testing
- `kani` — bounded model checking
- `googletest` assertions (if available)
- `pretty_assertions` — better diff output

### Configuration

- `ortho_config` — layered configuration for test setup
- Makefile targets: `make test`, `make lint`, `make typecheck`, `make
  check-fmt`

## Related Documentation

- `docs/developers-guide.md` — internal architecture
- `docs/users-guide.md` — user-facing documentation
- `docs/ortho-config-users-guide.md` — layered configuration
- `docs/rstest-bdd-users-guide.md` — BDD testing patterns
- `docs/rust-testing-with-rstest-fixtures.md` — rstest patterns
- `docs/reliable-testing-in-rust-via-dependency-injection.md` — dependency
  injection

## Reference Links

- [GitHub Actions Conditional Execution](
  https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#jobsjob_idif)
- [Ninja Build System](https://ninja-build.org/)
- [rstest Documentation](https://docs.rs/rstest/latest/rstest/)
- [insta Snapshot Testing](https://insta.rs/)
- [proptest Documentation](https://docs.rs/proptest/latest/proptest/)
- [Kani Bounded Model Checker](
  https://model-checking.github.io/kani/)
