# 3.2.5: Expose default reply templates as public API

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises &
Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept
up to date as work proceeds.

Status: DRAFT

## Purpose / big picture

Currently, default reply templates are defined as crate-private
implementations within a single module. This feature exposes those
templates through a stable, public API so downstream crates and
applications can programmatically access, inspect, and render default
reply messages without duplicating template definitions.

After this change, library users will be able to:

1. List all available default reply templates via a public function or
   enum.
2. Render a template with a set of context variables (deterministically).
3. Query template metadata (name, description, required variables).
4. Extend or compose templates for their own use cases.

Observable success: running `cargo test` passes all new tests; a
downstream application can import `reply_templates::presets` and call
`ReplyTemplate::GreetingMessage.render(&context)` to get a deterministic
string output.

## Constraints

Hard invariants that must hold throughout implementation:

- Public template definitions and rendering logic must be deterministic:
  identical inputs always produce identical outputs, byte-for-byte.
- All public template types must be thread-safe (implement `Send + Sync`).
- The public API must not re-export or depend on framework-specific
  types; domain types only.
- Template rendering must never fail for valid templates with correctly
  formed context (infallible in the happy path).
- No breaking changes to existing public APIs; this is purely additive.
- No external dependencies for template rendering (keep the core library
  minimal).
- All template content and metadata must remain immutable once published.

## Tolerances (exception triggers)

Thresholds that trigger escalation when breached:

- Scope: if implementation requires changes to more than 8 files or more
  than 500 lines of net code (excluding tests and docs), stop and
  escalate.
- Dependencies: if a new runtime dependency is required, stop and
  escalate (build-only deps are acceptable).
- Test coverage: if any milestone leaves new code with less than 85%
  line coverage, stop and escalate.
- Interface churn: if the public API signature must change after being
  documented, stop and escalate.
- Time: if any milestone takes more than 8 hours, stop and escalate.

## Risks

Known uncertainties that might affect the plan:

- Risk: Existing crate-private template implementations may have subtle
  parsing or substitution logic that is not immediately obvious from
  the code.
  Severity: medium
  Likelihood: medium
  Mitigation: Conduct a thorough audit of the existing template
  implementations (Stage 1); document all substitution rules and edge
  cases before designing the public API.

- Risk: Template context (variables) may contain data structures that
  are difficult to serialize or render deterministically.
  Severity: medium
  Likelihood: low
  Mitigation: Design the TemplateContext type to use ordered collections
  (BTreeMap) and simple scalar types; validate determinism with
  snapshot tests.

- Risk: Snapshot tests (insta) may produce large diffs if template
  content changes, making review difficult.
  Severity: low
  Likelihood: medium
  Mitigation: Keep template snapshots in a separate directory with clear
  naming; review snapshots as code changes, not as test artifacts.

- Risk: Hexagonal architecture boundaries may be unclear if templates
  are used in both domain and adapter layers.
  Severity: medium
  Likelihood: medium
  Mitigation: Define the template module as a domain port (interface);
  adapters implement the rendering logic; validate with architecture
  linting.

## Progress

Use a list with checkboxes to summarise granular steps:

- [ ] Stage 1: Audit and document existing template implementations.
- [ ] Stage 2: Design the public API types and module structure.
- [ ] Stage 3: Write Red tests for the public API.
- [ ] Stage 4: Implement the public API and template rendering logic.
- [ ] Stage 5: Add comprehensive unit and property-based tests.
- [ ] Stage 6: Add behavioral (BDD) tests.
- [ ] Stage 7: Add snapshot tests and integration tests.
- [ ] Stage 8: Document the feature and update user/developer guides.
- [ ] Stage 9: Code review and validation gates.
- [ ] Stage 10: Final testing and branch cleanup.

## Surprises & discoveries

Unexpected findings during implementation will be recorded here as work proceeds.

(To be updated during implementation.)

## Decision log

Record every significant decision made while working on the plan:

- Decision: Use an exhaustive enum for ReplyTemplate variants rather
  than a trait object or factory pattern.
  Rationale: Exhaustive enums provide type safety, zero-cost
  abstractions, and make it impossible for users to accidentally create
  invalid templates. Trait objects would add indirection; factory
  patterns would be less discoverable.
  Date/Author: 2026-06-18 (planning phase).

- Decision: Template rendering returns String, not Result<String, Error>.
  Rationale: Well-formed templates with valid context should never fail.
  Errors indicate programmer mistakes (missing variables), which should
  be caught at compile time or by tests, not at runtime. This keeps the
  API simple and matches the pattern of format! macros.
  Date/Author: 2026-06-18 (planning phase).

- Decision: Use BTreeMap for TemplateContext instead of HashMap for
  deterministic substitution ordering.
  Rationale: BTreeMap ensures consistent ordering across platforms and
  runs, which is essential for snapshot tests and determinism
  guarantees.
  Date/Author: 2026-06-18 (planning phase).

- Decision: Template module structure: `src/domain/templates/mod.rs`
  defines public types; `src/domain/templates/presets.rs` defines
  default templates.
  Rationale: Hexagonal architecture requires domain types to live in the
  domain layer; adapters (HTTP handlers, CLI output) depend on the
  public interface, not on the presets. This allows future expansion
  without boundary violations.
  Date/Author: 2026-06-18 (planning phase).

## Context and orientation

This feature lives in the domain layer of the crate. The crate is a Rust
library for managing comment replies, with templates defining the default
message formats.

Key files and modules (full paths):

- `src/domain/templates/mod.rs` — public template types (to be created).
- `src/domain/templates/presets.rs` — default template definitions (to
  be created).
- `src/domain/templates/errors.rs` — template-related errors (to be
  created).
- `src/lib.rs` — public crate exports.
- `tests/templates.rs` — integration tests for template rendering (to be
  created).
- `docs/developers-guide.md` — developer documentation (to be updated).
- `docs/users-guide.md` — user-facing API documentation (to be updated).

**Current state:** Templates are currently crate-private, defined in an
internal module with hardcoded substitution logic.

**Terminology:**

- **Template**: A message format with placeholders for context variables.
- **TemplateContext**: A map of variable names to values that fill
  placeholders during rendering.
- **Rendering**: The process of substituting context variables into a
  template to produce a final message.
- **Preset**: A named, predefined template (e.g., "GreetingMessage",
  "ErrorResponse").
- **Determinism**: The guarantee that rendering the same template with
  the same context always produces identical output.

## Plan of work

The implementation proceeds in stages, each with validation gates. Each
stage is small, testable, and keeps changes incremental.

### Stage 1: Audit and document existing template implementations

**Objective:** Understand what templates exist, how they are defined,
and what substitution logic they use.

**Work:**

1. Read the existing crate-private template implementations to identify
   all template variants, their content, and any hardcoded substitution
   patterns.
2. Document the following for each template:
   - Template name and purpose.
   - Variable placeholders (e.g., `{{name}}`, `${email}`).
   - Required and optional context variables.
   - Any edge cases or special handling (e.g., escaping, formatting).
3. Create a summary document at `docs/architecture/templates-audit.md`
   listing all templates, their purpose, and context requirements.

**Validation:** The audit document is complete and reviewed. All
templates are accounted for. Move to Stage 2.

### Stage 2: Design the public API types and module structure

**Objective:** Define the public types, module structure, and error
handling for template access and rendering.

**Work:**

1. Create `src/domain/templates/mod.rs` with the following public types:

   ```rust
   /// A default reply template variant.
   #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
   pub enum ReplyTemplate {
       GreetingMessage,
       ErrorResponse,
       ConfirmationMessage,
       // ... additional variants based on audit
   }

   /// Context variables for template rendering.
   pub struct TemplateContext {
       // Using BTreeMap for deterministic ordering
       variables: std::collections::BTreeMap<String, String>,
   }

   /// Metadata about a template.
   #[derive(Debug, Clone)]
   pub struct TemplateMetadata {
       pub id: &'static str,
       pub name: &'static str,
       pub description: &'static str,
       pub required_variables: &'static [&'static str],
   }

   /// Template-related errors.
   #[derive(Debug, Clone)]
   pub enum TemplateError {
       MissingVariable(String),
       InvalidTemplate(String),
   }
   ```

2. Create `src/domain/templates/presets.rs` with:
   - Const implementations of each template using `&'static str`.
   - Functions to look up templates by name or ID.
   - Template content as module-level constants.

3. Update `src/lib.rs` to re-export the public template types:

   ```rust
   pub use crate::domain::templates::{
       ReplyTemplate, TemplateContext, TemplateMetadata
   };
   ```

**Validation:** Code compiles without errors. Public API is accessible
and documented. Move to Stage 3.

### Stage 3: Write Red tests for the public API

**Objective:** Define test cases that verify the public API behavior
before implementing the core logic.

**Work:**

1. Create `tests/templates_unit.rs` with unit tests using `rstest`:

   ```rust
   #[rstest]
   fn test_template_render_greeting(
       #[values(ReplyTemplate::GreetingMessage)]
       template: ReplyTemplate,
   ) {
       let mut ctx = TemplateContext::new();
       ctx.set("name", "Alice");
       let result = template.render(&ctx);
       assert!(!result.is_empty());
       assert!(result.contains("Alice"));
   }

   #[rstest]
   fn test_template_determinism() {
       let mut ctx = TemplateContext::new();
       ctx.set("name", "Alice");
       let template = ReplyTemplate::GreetingMessage;
       let result1 = template.render(&ctx);
       let result2 = template.render(&ctx);
       assert_eq!(
           result1, result2,
           "Rendering must be deterministic"
       );
   }

   #[rstest]
   fn test_template_context_preserves_ordering() {
       let mut ctx = TemplateContext::new();
       ctx.set("z", "last");
       ctx.set("a", "first");
       // Verify deterministic ordering (BTreeMap)
       let vars = ctx.variables();
       assert_eq!(vars[0], ("a", "first"));
       assert_eq!(vars[1], ("z", "last"));
   }
   ```

2. Create `tests/templates_bdd.rs` with BDD scenarios using `cucumber`
   or `rstest-bdd`:

   ```gherkin
   Feature: Reply template rendering

     Scenario: Render greeting message with context
       Given a GreetingMessage template
       And context variable name="Alice"
       When I render the template
       Then the output contains "Alice"
       And the output is deterministic
   ```

3. Run all new tests (they will fail):

   ```bash
   cargo test --test templates_unit -- --nocapture
   ```

   Expected output: All new tests fail with assertion errors or
   compilation errors about missing implementations.

**Validation:** All tests fail for the expected reasons (missing
implementations). Move to Stage 4.

### Stage 4: Implement the public API and template rendering logic

**Objective:** Implement the minimum code to make all Red tests pass.

**Work:**

1. Implement `ReplyTemplate::render(&self, context: &TemplateContext)
   -> String`:
   - Use simple placeholder substitution (e.g., `{{variable}}` →
     context value).
   - Preserve exact template content from audit (Stage 1).
   - Panic on missing variables (caught by tests; static analysis in
     production).

2. Implement `TemplateContext` builder methods:
   - `new() -> Self`
   - `set(&mut self, key: &str, value: &str)`
   - `variables(&self) -> Vec<(&str, &str)>` (for test inspection)

3. Implement `ReplyTemplate` methods:
   - `metadata(&self) -> TemplateMetadata`
   - `list_all() -> Vec<ReplyTemplate>`

4. Run tests:

   ```bash
   cargo test --test templates_unit
   ```

   Expected: All unit tests pass.

**Validation:** All Red tests now pass (Green phase complete). Move to
Stage 5.

### Stage 5: Add comprehensive unit and property-based tests

**Objective:** Ensure the implementation is robust against edge cases
and property-based attacks.

**Work:**

1. Add property-based tests using `proptest`:

   ```rust
   proptest! {
       #[test]
       fn prop_template_render_is_deterministic(
           context in prop_template_context_strategy()
       ) {
           let template = ReplyTemplate::GreetingMessage;
           let result1 = template.render(&context);
           let result2 = template.render(&context);
           prop_assert_eq!(result1, result2);
       }

       #[test]
       fn prop_template_render_never_panics(
           template in any::<ReplyTemplate>(),
           context in prop_template_context_strategy()
       ) {
           let _ = template.render(&context);
       }
   }
   ```

2. Add edge case tests using `rstest`:

   ```rust
   #[rstest]
   #[case("", "")]
   #[case("key", "")]
   #[case("verylongkey", &"x".repeat(10000))]
   fn test_template_handles_edge_cases(
       #[case] key: &str,
       #[case] value: &str,
   ) {
       let mut ctx = TemplateContext::new();
       ctx.set(key, value);
       let result = ReplyTemplate::GreetingMessage.render(&ctx);
       assert!(!result.is_empty());
   }
   ```

3. Run tests with coverage:

   ```bash
   cargo tarpaulin --out Html --output-dir coverage/
   ```

   Expected: At least 85% line coverage for the templates module.

**Validation:** All property-based tests pass and coverage threshold is
met. Move to Stage 6.

### Stage 6: Add behavioral (BDD) tests

**Objective:** Verify that the feature behaves correctly from a user's
perspective using feature specifications.

**Work:**

1. Implement the BDD scenarios from Stage 3 using `rstest-bdd` or
   `cucumber`:

   ```rust
   #[given("a GreetingMessage template")]
   fn step_greeting_template(world: &mut TemplateWorld) {
       world.template = Some(ReplyTemplate::GreetingMessage);
   }

   #[when("I render the template")]
   fn step_render_template(world: &mut TemplateWorld) {
       let template = world.template.unwrap();
       world.rendered = Some(template.render(&world.context));
   }

   #[then("the output contains {string}")]
   fn step_output_contains(world: &TemplateWorld, expected: String) {
       assert!(world.rendered.as_ref().unwrap().contains(&expected));
   }
   ```

2. Add integration tests that verify end-to-end workflows:

   ```rust
   #[test]
   fn test_application_can_fetch_and_render_templates() {
       let templates = ReplyTemplate::list_all();
       assert!(!templates.is_empty());

       for template in templates {
           let metadata = template.metadata();
           assert!(!metadata.id.is_empty());

           let mut ctx = TemplateContext::new();
           for var in metadata.required_variables {
               ctx.set(var, "test-value");
           }
           
           let result = template.render(&ctx);
           assert!(!result.is_empty());
       }
   }
   ```

3. Run all tests:

   ```bash
   cargo test
   ```

   Expected: All tests pass.

**Validation:** BDD scenarios execute and pass. Integration tests
verify end-to-end behavior. Move to Stage 7.

### Stage 7: Add snapshot tests and final validation tests

**Objective:** Ensure template content is stable and prevent accidental
changes.

**Work:**

1. Add snapshot tests using `insta`:

   ```rust
   #[rstest]
   #[case(ReplyTemplate::GreetingMessage)]
   #[case(ReplyTemplate::ErrorResponse)]
   fn test_template_content_snapshot(
       #[case] template: ReplyTemplate
   ) {
       let mut ctx = TemplateContext::new();
       for var in template.metadata().required_variables {
           ctx.set(var, "test-value");
       }
       let result = template.render(&ctx);
       insta::assert_snapshot!(result);
   }
   ```

2. Generate and review snapshots:

   ```bash
   cargo test --test templates_snapshots -- --nocapture
   INSTA_REVIEW_MODE=overwrite cargo test --test templates_snapshots
   ```

3. Add a validation test that ensures all defaults are non-empty and
   deterministic:

   ```rust
   #[test]
   fn test_all_defaults_are_non_empty_and_deterministic() {
       for template in ReplyTemplate::list_all() {
           let mut ctx = TemplateContext::new();
           for var in template.metadata().required_variables {
               ctx.set(var, "test");
           }

           let result1 = template.render(&ctx);
           let result2 = template.render(&ctx);

           assert!(!result1.is_empty(), "Template {:?} empty", template);
           assert_eq!(
               result1, result2,
               "Template {:?} not deterministic",
               template
           );
       }
   }
   ```

4. Run all tests:

   ```bash
   cargo test
   cargo test --doc
   ```

   Expected: All tests pass, including snapshot tests.

**Validation:** Snapshots are reviewed and committed. All validation
tests pass. Move to Stage 8.

### Stage 8: Document the feature and update guides

**Objective:** Ensure the public API is well-documented for library
users and developers.

**Work:**

1. Add comprehensive doc comments to `src/domain/templates/mod.rs`:

   ```rust
   /// A default reply template.
   ///
   /// Templates are predefined message formats with placeholders
   /// for context variables. Each template is identified by a
   /// unique variant and renders deterministically given the
   /// same context.
   ///
   /// # Examples
   ///
   /// ```
   /// use crate::domain::templates::{
   ///     ReplyTemplate, TemplateContext
   /// };
   ///
   /// let mut ctx = TemplateContext::new();
   /// ctx.set("name", "Alice");
   /// let message = ReplyTemplate::GreetingMessage.render(&ctx);
   /// assert!(message.contains("Alice"));
   /// ```
   ///
   /// # Thread safety
   ///
   /// All template types are `Send + Sync` and can be safely
   /// shared across threads.
   pub enum ReplyTemplate {
       // ...
   }
   ```

2. Update `docs/users-guide.md` with:
   - A section on using default templates.
   - Examples of fetching, rendering, and composing templates.
   - Guarantees (determinism, thread safety).
   - How to extend or compose templates.

3. Update `docs/developers-guide.md` with:
   - Module architecture (domain templates, preset constants).
   - How to add a new template variant.
   - Testing guidelines for templates.
   - Determinism requirements and verification.

4. Create or update `docs/adr/adr-005-public-template-api.md` (ADR):
   - Why templates are now public.
   - Design decisions (enum-based, infallible rendering, BTreeMap
     ordering).
   - Backward compatibility strategy.
   - Future extensibility (custom templates, serialization).

**Validation:** Documentation is complete and reviewed. Examples
compile and run. Move to Stage 9.

### Stage 9: Code review and validation gates

**Objective:** Ensure all code passes linting, type-checking, and
automated quality gates.

**Work:**

1. Run all code quality gates:

   ```bash
   make check-fmt       # Format checking
   make lint            # Linting (clippy)
   make test            # All tests
   cargo doc --no-deps  # Documentation generation
   ```

   Expected: All gates pass with no warnings or errors.

2. Run CodeRabbit review (if enabled):

   ```bash
   coderabbit review --agent
   ```

   Expected: No critical issues; all findings reviewed and resolved.

3. Commit changes with a clear message:

   ```bash
   git add -A
   git commit -m \
     "feat: Expose default reply templates as public library API

   - Add ReplyTemplate enum with all default variants
   - Add TemplateContext for context variable management
   - Add TemplateMetadata for template introspection
   - Implement deterministic rendering with snapshot tests
   - Update docs/users-guide.md and docs/developers-guide.md
   - Add comprehensive unit, property-based, BDD, and tests
   - Ensure thread safety (Send + Sync) for all public types

   Closes #3-2-5"
   ```

**Validation:** All gates pass. Code review is complete. Move to
Stage 10.

### Stage 10: Final testing and branch cleanup

**Objective:** Verify the complete feature works end-to-end and prepare
for merge.

**Work:**

1. Run the full test suite one final time:

   ```bash
   cargo test --all-features
   cargo doc --no-deps --open
   ```

2. Verify that a downstream application can use the new public API:

   ```rust
   use reply_lib::domain::templates::{
       ReplyTemplate, TemplateContext
   };

   fn main() {
       let mut ctx = TemplateContext::new();
       ctx.set("user", "Bob");

       let greeting = ReplyTemplate::GreetingMessage.render(&ctx);
       println!("Greeting: {}", greeting);
   }
   ```

3. Create a summary of changes in the PR description (when ready to
   merge):
   - Link to the execplan document.
   - List all new public types and functions.
   - Summarize test coverage.
   - Note backward compatibility implications (none; additive only).

4. Update the roadmap (docs/roadmap.md if it exists, or equivalent) to
   mark feature 3.2.5 as "done".

**Validation:** Feature is complete, tested, and documented. Ready for
merge.

## Validation and acceptance

### Quality criteria

**Tests:** All tests pass, including:

- Unit tests (rstest): 30+ tests covering all template variants, edge
  cases, and determinism.
- Property-based tests (proptest): Verify idempotence, non-panic
  behavior, and output stability.
- BDD tests (rstest-bdd or cucumber): Feature scenarios for template
  rendering and context handling.
- Integration tests: End-to-end workflows that verify library users can
  fetch and render templates.
- Snapshot tests (insta): Ensure template content does not change
  unexpectedly.

**Lint/typecheck:**

- `cargo check` passes with no warnings.
- `cargo clippy -- -D warnings` passes.
- `cargo fmt --check` passes.

**Documentation:**

- Doc comments on all public types and functions.
- Updated docs/users-guide.md with examples.
- Updated docs/developers-guide.md with implementation details.
- ADR document explaining design decisions.

**Coverage:**

- At least 85% line coverage for src/domain/templates/.
- At least 85% branch coverage for core rendering logic.

### How to verify success

1. Run the test suite: `cargo test --all`
   - Expected: All tests pass (60+ tests total).

2. Generate documentation: `cargo doc --no-deps --open`
   - Expected: All public items are documented with examples.

3. Verify determinism:

   ```bash
   cargo test --test templates_snapshots -- --nocapture
   ```

   - Expected: All snapshot tests pass; no unexpected changes to
     template content.

4. Verify end-to-end: Create a simple binary that imports and uses the
   public API:

   ```bash
   cargo new --bin test_templates
   # Add to Cargo.toml: reply_lib = { path = "../" }
   # Write a main.rs that uses ReplyTemplate::GreetingMessage
   cargo run
   ```

   - Expected: Binary compiles and prints a greeting message.

5. Verify backward compatibility: `cargo test --all` in the parent
   project.
   - Expected: No existing tests fail; only new tests are added.

## Idempotence and recovery

All steps are idempotent and can be safely re-run:

- Running `cargo test` multiple times produces the same results.
- Running `cargo fmt` multiple times does not change the code.
- Snapshot tests can be reviewed and approved by re-running with
  `INSTA_REVIEW_MODE=overwrite`.
- If a test fails, run it in isolation:
  `cargo test --test templates_unit test_name -- --nocapture`.

If a milestone fails mid-way (e.g., a test fails), fix the issue and
re-run the stage:

1. Identify the failing test or validation step.
2. Fix the code or test.
3. Re-run the stage from the beginning.
4. Commit the fix with a descriptive message.

No rollback is needed; changes are incremental and safe.

## Interfaces and dependencies

### Public interfaces (to be created)

**Module:** `crate::domain::templates`

**Types:**

- `ReplyTemplate` (enum): All default template variants.
  - Variant examples: `GreetingMessage`, `ErrorResponse`,
    `ConfirmationMessage`.
  - Methods: `render(&self, context: &TemplateContext) -> String`,
    `metadata(&self) -> TemplateMetadata`, `list_all() ->
    Vec<ReplyTemplate>`.

- `TemplateContext` (struct): Holds context variables for rendering.
  - Methods: `new() -> Self`, `set(&mut self, key: &str, value: &str)`.
  - Internal: Uses `BTreeMap<String, String>` for deterministic
    ordering.

- `TemplateMetadata` (struct): Describes a template.
  - Fields: `id: &'static str`, `name: &'static str`, `description:
    &'static str`, `required_variables: &'static [&'static str]`.

- `TemplateError` (enum): Template-related errors.
  - Variants: `MissingVariable(String)`, `InvalidTemplate(String)`.

**Re-exports in `crate::lib.rs`:**

```rust
pub use crate::domain::templates::{
    ReplyTemplate, TemplateContext, TemplateMetadata, TemplateError
};
```

### Dependencies

**Build dependencies:**

- `rstest` (dev-dependency): For parameterized unit tests.
- `proptest` (dev-dependency): For property-based tests.
- `insta` (dev-dependency): For snapshot tests.
- `pretty_assertions` (dev-dependency): For clear assertion messages.
- `googletest` (dev-dependency): For rich test assertions.

**Runtime dependencies:**

- None (core implementation uses only std library).

**Optional/future:**

- `serde` (optional feature): For template serialization (not in
  initial scope).
- `askama` or similar (optional feature): For advanced template syntax
  (not in initial scope).

## Artifacts and notes

### Summary of changes

1. **New files:**
   - `src/domain/templates/mod.rs` (~200 LOC): Public API types and
     rendering logic.
   - `src/domain/templates/presets.rs` (~300 LOC): Default template
     content and constants.
   - `src/domain/templates/errors.rs` (~50 LOC): Error types.
   - `tests/templates_unit.rs` (~200 LOC): Unit tests.
   - `tests/templates_bdd.rs` (~150 LOC): BDD scenarios.
   - `tests/templates_snapshots.rs` (~100 LOC): Snapshot tests.
   - `tests/templates_integration.rs` (~150 LOC): End-to-end tests.
   - `docs/adr/adr-005-public-template-api.md` (~300 LOC): Architecture
     decision record.

2. **Updated files:**
   - `src/lib.rs` (~5 LOC): Add public re-exports.
   - `docs/users-guide.md`: Add section on using templates (~200 LOC).
   - `docs/developers-guide.md`: Add implementation details (~150 LOC).
   - `.gitignore`: Exclude snapshot backups (if needed).

3. **Total new code (excluding tests and docs):** ~500 LOC.
4. **Total test code:** ~600 LOC.
5. **Total documentation:** ~650 LOC.

### Example: Using the public template API

```rust
use reply_lib::domain::templates::{ReplyTemplate, TemplateContext};

fn main() {
    for template in ReplyTemplate::list_all() {
        println!("Template: {}", template.metadata().name);
    }

    let mut context = TemplateContext::new();
    context.set("name", "Alice");
    context.set("date", "2026-06-18");

    let message = ReplyTemplate::GreetingMessage.render(&context);
    println!("{}", message);
}
```

---

## Revision note

(To be updated as the plan is revised during implementation.)
