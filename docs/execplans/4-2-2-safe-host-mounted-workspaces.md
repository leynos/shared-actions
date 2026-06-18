# 4.2.2 Implement Safe Host-Mounted Workspaces

This ExecPlan is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date
as work proceeds.

Status: APPROVED

## Purpose / big picture

This milestone implements safe host-mounted workspaces—the mechanism by
which operators can mount host directories into container sessions while
maintaining strict boundaries against symlink escapes, unauthorized path
access, and privilege escalation vectors.

After this change, users will be able to configure host-mounted
workspaces with confidence that all mount paths are canonicalized,
mounts are confined to allowlisted roots, write permissions are
validated, the threat model is documented, and forbidden paths are
rejected even under adversarial conditions.

Success is observable when:
1. Running `cargo test` in `tests/workspace_mounts_tests.rs` shows ≥40 tests passing with ≥90% coverage
2. BDD scenarios in `tests/bdd/workspace_mounts.feature` all pass
3. Documentation in `docs/users-guide.md` and `docs/developers-guide.md` describes configuration and threat model
4. `make check-fmt`, `make lint`, and `make test` all pass without errors
5. A PR against main is created with all gates passing

## Constraints

Hard invariants that must hold throughout implementation.

- **No public API breaking changes**: Existing container launch signatures must remain stable. New validation functions are internal to workspace_mounts module.
- **No new external crate dependencies**: Use only Rust std::fs, std::path, and existing project dependencies (thiserror for error handling, tracing for logging).
- **Rootless Podman compatibility**: All validation must work correctly in rootless namespaces without requiring uid 0.
- **Backward compatibility**: Existing workspace configurations must continue to work; validation is additive.
- **Zero symlinks in validated paths**: After canonicalization, no symlinks may exist in any component of the validated path.
- **Domain logic has no I/O**: All filesystem operations pass through FilesystemPort trait; domain validator is pure logic, all I/O happens in adapters.
- **Hexagonal architecture maintained**: Dependency graph must point inward; ports defined at domain boundary; adapters implement ports, never call each other.
- **All errors are domain-owned**: WorkspaceMountError enum is exhaustive; infrastructure errors converted to domain errors at adapter boundaries.

## Tolerances (exception triggers)

Thresholds that trigger escalation when breached.

- **Scope**: ≤20 files modified, ≤3000 net lines of code added. If exceeded, stop and present impact analysis.
- **Dependencies**: Any new external crate must be escalated. Standard library only, unless project already uses a crate (e.g., thiserror, tracing).
- **Interface changes**: If any public API signature in existing modules must change, stop and escalate with rationale.
- **Test coverage**: Domain logic must reach ≥90% line coverage and ≥90% branch coverage. If coverage gaps exist after Phase 5, stop and investigate.
- **Build/test failures**: If make test, make lint, or make check-fmt fails after implementation, stop and fix before proceeding.
- **Threat model disagreement**: If during implementation the threat model interpretation differs materially from the documented 7 attack vectors, escalate with evidence.
- **Time**: If any single phase takes >6 hours, document progress and reassess plan feasibility.

## Risks

Known uncertainties that might affect the plan.

- **TOCTOU race: symlink added after validation but before mount**
  Severity: High | Likelihood: Medium | Mitigation: Minimize validation window; use `canonicalize()` + re-check symlinks immediately before mount; document assumption that operator controls allowlist roots
  
- **Permission validation unreliable in rootless: UID/GID remapping**
  Severity: High | Likelihood: High | Mitigation: Add RootlessPermissionAdapter that uses `libc::geteuid()` and `libc::getegid()` to validate namespace context; write integration tests with `podman unshare`
  
- **Symlink cycles cause infinite loop during depth traversal**
  Severity: Medium | Likelihood: Low | Mitigation: Set hard limit of 40 components per path; detect cycles explicitly in OsFilesystemAdapter.has_symlinks()
  
- **Allowlist prefix attacks: /tmp/workspaces_evil matches /tmp/workspaces**
  Severity: High | Likelihood: Low | Mitigation: Validate component boundaries, not just string prefix; use `path.starts_with(root) && (path.len() == root.len() || path[root.len()] == b'/')` pattern
  
- **Mount flag escapes via remount/shared flags**
  Severity: Medium | Likelihood: Low | Mitigation: Future enhancement; document in roadmap; for now assume container runtime enforces safe flags
  
- **Test suite insufficient**: Coverage tools may miss branches
  Severity: Medium | Likelihood: Medium | Mitigation: Use `cargo tarpaulin --out Html` for detailed branch coverage; manually inspect untested paths; use property tests (proptest) for idempotence guarantees
  
- **Integration test environment differences**: Tests pass locally but fail in CI with different rootless setup
  Severity: Medium | Likelihood: Medium | Mitigation: Test both as regular user and with `podman unshare`; document platform assumptions; skip tests that require rootless if not available

## Progress

Use checkboxes to track granular steps with timestamps. Each stage must complete validation before proceeding.

- [ ] **Stage 1: Threat model & architecture documentation** (Est. 2-3 hours)
  - [ ] (TBD) Create docs/architecture/04-workspace-mounts-threat-model.md
  - [ ] (TBD) Create docs/architecture/05-workspace-mounts-hexagonal-design.md
  - [ ] (TBD) Create docs/adr/0003-workspace-mount-validation.md
  - [ ] (TBD) Validation: Threat model is unambiguous, design approved

- [ ] **Stage 2: Module structure & port traits** (Est. 1-2 hours)
  - [ ] (TBD) Create src/workspace_mounts/ directory structure
  - [ ] (TBD) Define all port traits (Filesystem, Permission, Config, ThreatReporter)
  - [ ] (TBD) Define value object stubs
  - [ ] (TBD) Validation: `cargo check` passes

- [ ] **Stage 3: Error type & value objects (Red-Green-Refactor)** (Est. 1-2 hours)
  - [ ] (TBD) Write Red: Unit tests for all 10 error variants
  - [ ] (TBD) Write Green: Implement WorkspaceMountError, ValidatedWorkspacePath, AllowedWorkspaceRoot
  - [ ] (TBD) Refactor: Document and improve error messages
  - [ ] (TBD) Validation: `cargo test --lib workspace_mounts` all pass

- [ ] **Stage 4: FilesystemPort & OsFilesystemAdapter (Red-Green-Refactor)** (Est. 2-3 hours)
  - [ ] (TBD) Write Red: Unit & integration tests for symlink detection, depth limit, cycles
  - [ ] (TBD) Write Green: Implement OsFilesystemAdapter with canonicalize, has_symlinks, metadata checks
  - [ ] (TBD) Refactor: Extract helpers, add property tests
  - [ ] (TBD) Validation: `cargo test --test integration` passes, ≥90% coverage

- [ ] **Stage 5: PermissionPort & RootlessPermissionAdapter (Red-Green-Refactor)** (Est. 2-3 hours)
  - [ ] (TBD) Write Red: Unit & integration tests for mode bits, UID/GID checks
  - [ ] (TBD) Write Green: Implement RootlessPermissionAdapter using libc
  - [ ] (TBD) Add rootless integration tests
  - [ ] (TBD) Validation: Works in both regular and rootless environments

- [ ] **Stage 6: Domain Validator (Pure logic, Red-Green-Refactor)** (Est. 3-4 hours)
  - [ ] (TBD) Write Red: Unit tests for all attack vectors, property tests for idempotence
  - [ ] (TBD) Write Green: Implement WorkspacePathValidator::validate()
  - [ ] (TBD) Refactor: Extract pure helpers, document assumptions
  - [ ] (TBD) Validation: ≥90% branch coverage, property tests pass

- [ ] **Stage 7: Integration, BDD scenarios, & application service (Red-Green-Refactor)** (Est. 3-4 hours)
  - [ ] (TBD) Write Red: BDD scenarios in tests/bdd/workspace_mounts.feature
  - [ ] (TBD) Write Green: Implement WorkspaceMountServiceImpl, BDD steps
  - [ ] (TBD) Add snapshot tests, stress tests
  - [ ] (TBD) Validation: All BDD scenarios pass, integration tests pass

- [ ] **Stage 8: Documentation & deployment guide** (Est. 2-3 hours)
  - [ ] (TBD) Update docs/users-guide.md with configuration and examples
  - [ ] (TBD) Update docs/developers-guide.md with architecture and testing
  - [ ] (TBD) Create docs/adr/0004-workspace-mount-validation-rationale.md
  - [ ] (TBD) Validation: Documentation builds, examples are correct

- [ ] **Stage 9: Quality gates & final validation** (Est. 2-3 hours)
  - [ ] (TBD) Run make check-fmt, make lint, cargo test
  - [ ] (TBD) Run cargo tarpaulin --out Html and verify ≥90% coverage
  - [ ] (TBD) Create PR and request CodeRabbit review
  - [ ] (TBD) Address review comments
  - [ ] (TBD) Update roadmap marking 4.2.2 as COMPLETE
  - [ ] (TBD) Validation: All gates pass, PR approved

## Surprises & discoveries

Unexpected findings during implementation. This section is updated as
work proceeds.

(None yet; will be populated during implementation.)

## Decision log

Record every significant decision made while working on the plan.

- Decision: Use Rust `std::fs::canonicalize()` as baseline
  Rationale: Stable, audited. Avoids external crates unless stdlib
  proves insufficient
  Date: 2026-06-18 (planning phase)

- Decision: Allowlist roots configured in YAML at startup
  Rationale: Enforces operator control. Errors detected at startup, not
  mount time
  Date: 2026-06-18 (planning phase)

- Decision: Permission validation uses `podman unshare` in rootless
  Rationale: UID/GID remapping makes naive checks unreliable. Testing
  in actual namespace is only reliable method
  Date: 2026-06-18 (planning phase)

(Additional decisions recorded during implementation.)

## Outcomes & retrospective

This section is populated at major milestones and at completion.

(To be updated as work proceeds.)

## Context and orientation

### Current state

Podbot is a container orchestration tool organized around composing container launches from reusable request/plan primitives. The codebase currently lacks specialized workspace mount validation, which is a gap for safe host-mounted workspace support.

**Related existing infrastructure**:
- `src/config.rs` — Configuration loading and validation
- `src/container.rs` — Container launch and execution
- `Cargo.toml` — Dependencies (thiserror for errors, tracing for logging likely available)
- Tests framework: cargo test (unit and integration)

**Current limitations**:
- No canonicalization of mount paths
- No allowlist enforcement
- No validation of mount permissions
- No threat model documentation
- Risk: Operators could accidentally mount dangerous paths (symlinks, parent dirs, read-only dirs)

### Project structure

The Podbot codebase uses a modular approach with clear separation between config, domain logic, and container execution.

### Implementation approach: Hexagonal architecture

This task uses **hexagonal architecture** (ports & adapters) to maintain clean separation:
- **Domain** (`src/workspace_mounts/domain/`) — Pure business logic, no I/O
- **Ports** (`src/workspace_mounts/domain/ports/`) — Abstract interfaces (what domain requires from infrastructure)
- **Adapters** (`src/workspace_mounts/adapters/`) — Concrete implementations (filesystem I/O, permission checks, config loading)
- **Application Service** (`src/workspace_mounts/lib.rs`) — Orchestrates domain + adapters for callers

This design ensures:
- ✓ Domain logic is testable without I/O (use mocks)
- ✓ Adapters are easy to replace or extend (e.g., add SELinux checks later)
- ✓ All dependencies point inward (domain ← adapters)
- ✓ Errors are domain-owned (not infrastructure-specific)

### Key files to create

**Domain logic** (no I/O):
- `src/workspace_mounts/domain/errors.rs` — WorkspaceMountError enum (10 variants)
- `src/workspace_mounts/domain/model.rs` — Value objects: ValidatedWorkspacePath, AllowedWorkspaceRoot, WorkspaceConfig
- `src/workspace_mounts/domain/validator.rs` — WorkspacePathValidator (pure logic)
- `src/workspace_mounts/domain/ports/filesystem.rs` — FilesystemPort trait
- `src/workspace_mounts/domain/ports/permission.rs` — PermissionPort trait
- `src/workspace_mounts/domain/ports/config.rs` — WorkspaceConfigPort trait
- `src/workspace_mounts/domain/ports/threat_reporter.rs` — ThreatReportPort trait

**Adapters** (with I/O):
- `src/workspace_mounts/adapters/os_filesystem.rs` — OsFilesystemAdapter (std::fs wrapper)
- `src/workspace_mounts/adapters/permission.rs` — RootlessPermissionAdapter (libc wrapper)
- `src/workspace_mounts/adapters/yaml_config.rs` — YamlConfigAdapter (YAML config loader)
- `src/workspace_mounts/adapters/logging.rs` — LoggingThreatReporter (tracing integration)

**Application service** (boundary):
- `src/workspace_mounts/lib.rs` — WorkspaceMountServiceImpl (orchestrates domain + adapters)

**Tests**:
- `tests/workspace_mounts_tests.rs` — Unit tests with mocks
- `tests/workspace_mounts_integration_tests.rs` — Integration tests with real filesystem
- `tests/bdd/workspace_mounts.feature` — Behavior-driven scenarios
- `tests/bdd/workspace_mounts_steps.rs` — BDD step implementations

**Documentation**:
- `docs/architecture/04-workspace-mounts-threat-model.md` — Security threat model
- `docs/architecture/05-workspace-mounts-hexagonal-design.md` — Architecture diagram & design
- `docs/adr/0003-workspace-mount-validation.md` — Architecture decision record
- `docs/users-guide.md` — Updated with configuration section
- `docs/developers-guide.md` — Updated with testing & architecture guidance

## Plan of work

Implementation proceeds through seven stages with explicit Red-Green-Refactor validation at each domain logic milestone. All code changes are small, testable, and committed after each stage passes quality gates.

### Stage 1: Threat model & architecture documentation

**Objective**: Design the domain model, ports, and adapters before writing code.

**Work**:
1. Create `docs/architecture/04-workspace-mounts-threat-model.md` documenting:
   - All 7 attack vectors (symlink escape, TOCTOU, path traversal, allowlist prefix bypass, permission escalation, rootless UID/GID mismatch, symlink cycles)
   - Prevention mechanisms for each
   - Explicit threat boundaries (what we control vs. what we delegate)
   - Assumptions (operator correctness, POSIX filesystem, atomic mounts)
2. Create `docs/architecture/05-workspace-mounts-hexagonal-design.md` with:
   - Domain model (WorkspaceMountError, ValidatedWorkspacePath, AllowedWorkspaceRoot, WorkspaceConfig)
   - Ports (FilesystemPort, PermissionPort, WorkspaceConfigPort, ThreatReportPort)
   - Adapters (OsFilesystemAdapter, RootlessPermissionAdapter, YamlConfigAdapter, LoggingThreatReporter)
   - Hexagonal diagram
3. Create `docs/adr/0003-workspace-mount-validation.md` recording architecture decisions

**Validation**: Threat model is unambiguous; hexagonal diagram is reviewed; no implementation has begun.

**Estimated effort**: 2-3 hours

### Stage 2: Module structure & port traits

**Objective**: Create the module skeleton and port trait definitions.

**Work**:
1. Create new module `src/workspace_mounts/` directory with:
   - `mod.rs` — exports public API
   - `domain/errors.rs` — WorkspaceMountError enum (10 variants)
   - `domain/model.rs` — Value objects (ValidatedWorkspacePath, AllowedWorkspaceRoot, WorkspaceConfig)
   - `domain/validator.rs` — Pure-logic domain service (WorkspacePathValidator)
   - `domain/ports/filesystem.rs` — FilesystemPort trait
   - `domain/ports/permission.rs` — PermissionPort trait
   - `domain/ports/config.rs` — WorkspaceConfigPort trait
   - `domain/ports/threat_reporter.rs` — ThreatReportPort trait
   - `adapters/os_filesystem.rs` — OsFilesystemAdapter
   - `adapters/permission.rs` — RootlessPermissionAdapter
   - `adapters/yaml_config.rs` — YamlConfigAdapter
   - `adapters/logging.rs` — LoggingThreatReporter
   - `lib.rs` or public types in `mod.rs` — WorkspaceMountServiceImpl (application service)

2. Write all trait signatures and struct definitions (no implementation bodies yet)

**Validation**: `cargo check` passes; no missing trait methods; module structure matches hexagonal design.

**Estimated effort**: 1-2 hours

### Stage 3: Error type & value objects (Red-Green-Refactor)

**Objective**: Define exhaustive error type and invariant-enforcing value objects.

**Red**: Write unit tests in `tests/workspace_mounts_tests.rs` that:
- Construct each of the 10 WorkspaceMountError variants
- Verify ValidatedWorkspacePath can only be created by validator
- Verify AllowedWorkspaceRoot rejects invalid paths at construction

**Green**: Implement:
- WorkspaceMountError with `#[derive(Debug, Clone, PartialEq)]` and thiserror::Error
- ValidatedWorkspacePath as newtype with private constructor; `pub fn new_unchecked()` for validator only
- AllowedWorkspaceRoot with validation at construction (path exists, is directory, is absolute)
- WorkspaceConfig as aggregate root (holds list of AllowedWorkspaceRoot)

**Refactor**: Ensure error Display messages are actionable; document each variant with example.

**Validation**: `cargo test --lib workspace_mounts` passes; 100% error type coverage; all error messages are tested.

**Estimated effort**: 1-2 hours

### Stage 4: FilesystemPort & OsFilesystemAdapter (Red-Green-Refactor)

**Objective**: Implement abstracted filesystem operations.

**Red**: Write unit tests with mocks:
- `test_canonicalize_rejects_symlinks_in_path`
- `test_canonicalize_resolves_dotdot`
- `test_has_symlinks_detects_all_components`
- `test_has_symlinks_respects_depth_limit`
- `test_has_symlinks_detects_cycles`

Write integration tests with real filesystem (using tempfile):
- Create actual symlink, verify detection
- Create symlink cycle, verify depth limit prevents DoS
- Create deeply nested symlinks, verify limit respected

**Green**: Implement:
- FilesystemPort trait with methods: `canonicalize()`, `has_symlinks()`, `read_metadata_nofollow()`, `exists()`
- OsFilesystemAdapter using std::fs, std::path, fs::read_link()
- Depth limit constant (40 components)
- Cycle detection via visited set

**Refactor**: Extract helper functions; document assumptions; add property tests for idempotence.

**Validation**: `cargo test --test integration filesystem` passes; `cargo tarpaulin` shows ≥90% branch coverage for adapter.

**Estimated effort**: 2-3 hours

### Stage 5: PermissionPort & RootlessPermissionAdapter (Red-Green-Refactor)

**Objective**: Implement permission validation compatible with rootless containers.

**Red**: Write unit tests with mocks:
- `test_is_writable_checks_mode_bits`
- `test_is_writable_rejects_read_only`
- `test_container_ids_returns_euid_egid`
- `test_rootless_context_validation_succeeds_in_namespace`

Write integration tests:
- Set path mode to 0o444, verify writable check rejects
- Run in `podman unshare` namespace, verify UID/GID checks work

**Green**: Implement:
- PermissionPort trait with methods: `is_writable()`, `container_ids()`, `check_security_context()`
- RootlessPermissionAdapter using libc::geteuid(), libc::getegid()
- Mode bit checking: `(stat.st_mode & 0o200) != 0`

**Refactor**: Document namespace assumptions; add error context showing actual vs. expected UID.

**Validation**: `cargo test --test integration permission` passes; works in both regular and rootless environments.

**Estimated effort**: 2-3 hours

### Stage 6: Domain Validator (Pure logic, Red-Green-Refactor)

**Objective**: Implement pure-logic domain validator without I/O.

**Red**: Write comprehensive unit tests with mocks:
- Three-stage validation passes for valid paths
- Symlink escape attempt rejected at canonicalize step
- Path traversal (/../..) rejected at canonicalize step
- Allowlist prefix attack (/tmp/workspaces_evil vs /tmp/workspaces) rejected
- Permission check catches read-only directories
- TOCTOU windows are minimized (re-check after external operations)

Property tests:
- `validate(validate(path))` returns same result (idempotence)
- Allowlist member check is consistent (same path always passes/fails)

**Green**: Implement WorkspacePathValidator::validate():
```rust
pub fn validate(&self, path: &Path) -> Result<ValidatedWorkspacePath> {
    // Step 1: Canonicalize (rejects symlinks)
    let canonical = self.filesystem.canonicalize(path)?;
    
    // Step 2: Enforce allowlist (checks component boundaries)
    self.check_in_allowlist(&canonical)?;
    
    // Step 3: Validate permissions (checks writable)
    self.validate_permissions(&canonical)?;
    
    // Step 4: Report success
    self.threat_reporter.report_mount_approved(&canonical);
    Ok(ValidatedWorkspacePath::new_unchecked(canonical))
}
```

**Refactor**: Extract pure helper methods; document assumptions; add error context.

**Validation**: `cargo test --lib validator` passes; ≥90% branch coverage; property tests pass.

**Estimated effort**: 3-4 hours

### Stage 7: Integration, BDD scenarios, & application service (Red-Green-Refactor)

**Objective**: Integrate all components; write E2E tests; expose public API.

**Red**: Write BDD scenarios in `tests/bdd/workspace_mounts.feature`:
```gherkin
Feature: Safe host-mounted workspaces
  Scenario: Valid workspace path is validated successfully
    Given an allowed root "/tmp/workspaces"
    And a valid workspace path "/tmp/workspaces/job-123"
    When I validate the workspace mount
    Then the mount should be approved
    
  Scenario: Symlink escape attempt is rejected
    Given an allowed root "/tmp/workspaces"
    And a symlink "/tmp/workspaces/link" pointing to "/etc"
    When I validate the workspace mount for "/tmp/workspaces/link"
    Then the mount should be rejected with reason "symlink detected"
    
  Scenario: Path outside allowlist is rejected
    Given allowed roots ["/tmp/workspaces"]
    And a valid path "/home/attacker/workspace"
    When I validate the workspace mount
    Then the mount should be rejected with reason "not in allowlist"
```

Run `cargo test --test bdd` and watch all scenarios fail.

**Green**: Implement WorkspaceMountServiceImpl:
```rust
pub struct WorkspaceMountServiceImpl {
    validator: Arc<WorkspacePathValidator>,
}

impl WorkspaceMountServiceImpl {
    pub fn new(config, filesystem, permission, threat_reporter) -> Self { ... }
    pub fn validate_mount(&self, path: &Path) -> Result<PathBuf> { ... }
    pub fn validate_mounts(&self, paths: &[PathBuf]) 
        -> Result<Vec<PathBuf>, Vec<(PathBuf, WorkspaceMountError)>> { ... }
}
```

Write BDD step implementations (using cucumber or similar).

Add snapshot tests showing error message formatting for each error variant.

Add stress tests: validate 1000 paths with various attacks.

**Refactor**: Ensure error messages are consistent; add structured logging via ThreatReportPort; document composition pattern.

**Validation**: `cargo test --test bdd` all scenarios pass; `cargo test --test integration` all integration tests pass; `cargo test --lib` all unit tests pass.

**Estimated effort**: 3-4 hours

### Stage 8: Documentation & deployment guide

**Objective**: Document configuration, threat model, and deployment steps.

**Work**:
1. Update `docs/users-guide.md` with:
   - Configuration schema (YAML example)
   - Allowed roots setup and validation
   - Error messages and troubleshooting
   - Examples of valid and invalid paths
   
2. Update `docs/developers-guide.md` with:
   - Module architecture (domain, adapters)
   - How to add new adapters (e.g., SELinux context port)
   - Testing guidelines (unit, integration, BDD)
   - Security review checklist
   
3. Create `docs/adr/0004-workspace-mount-validation-rationale.md` with decisions and trade-offs

**Validation**: Documentation builds without errors; examples are tested (copy-paste works).

**Estimated effort**: 2-3 hours

### Stage 9: Quality gates & final validation

**Objective**: Run all checks and verify production readiness.

**Work**:
1. Run `make check-fmt` — all workspace_mounts code is formatted
2. Run `make lint` — no clippy warnings
3. Run `cargo test` — all tests pass
4. Run `cargo tarpaulin --out Html --timeout 300` — verify ≥90% coverage
5. Run `make check-docs` if available — documentation builds
6. Create draft PR and request CodeRabbit review
7. Address any review comments
8. Mark task as COMPLETE in roadmap

**Validation**: All gates pass; PR is ready for merge.

**Estimated effort**: 2-3 hours (including review turnaround)

## Validation and acceptance

### Acceptance criteria (all must be satisfied)

1. **Test coverage**: `cargo tarpaulin --out Html` shows ≥90% line coverage and ≥90% branch coverage for all workspace_mounts code
2. **Unit tests pass**: `cargo test --lib workspace_mounts` runs with no failures; tests exercise all 10 error variants
3. **Integration tests pass**: `cargo test --test integration workspace_mounts` passes; tests use real filesystem and tempfile
4. **BDD scenarios pass**: `cargo test --test bdd workspace_mounts` passes; all Gherkin scenarios in `tests/bdd/workspace_mounts.feature` pass
5. **Property tests pass**: Idempotence properties validated; `validate(validate(path))` always returns same result
6. **Quality gates**:
   - `make check-fmt` passes (all workspace_mounts code is formatted)
   - `make lint` passes (no clippy warnings)
   - `cargo test` (full suite) passes with no failures
7. **Documentation complete**:
   - `docs/users-guide.md` includes configuration section with examples
   - `docs/developers-guide.md` describes module architecture and testing strategy
   - `docs/architecture/04-workspace-mounts-threat-model.md` documents all threat vectors
   - `docs/adr/0003-workspace-mount-validation.md` records design decisions
8. **Configuration integration**: Podbot config loading accepts workspace_mounts configuration and validates at startup
9. **Rootless compatibility**: Tests pass in both regular and rootless (via `podman unshare`) environments
10. **PR created**: Branch pushed; PR created against main with all checks passing; CodeRabbit review completed

### Quality method: Red-Green-Refactor

For each domain logic component (error type, validator, adapters), follow this discipline:

**Red phase**:
```bash
# Write failing test(s) that specify desired behaviour
# For unit tests, use mocks; for integration, use real filesystem
cargo test --lib workspace_mounts -- --nocapture
# Expected: test fails with "assertion failed" or "not implemented"
```

**Green phase**:
```bash
# Implement minimal code to make test pass
# Prioritize correctness over optimization
cargo test --lib workspace_mounts -- --nocapture
# Expected: test passes
```

**Refactor phase**:
```bash
# Clean up implementation without changing behaviour
# Extract pure helpers, improve error messages, add documentation
cargo test --lib workspace_mounts -- --nocapture
# Expected: test still passes; code is cleaner
```

### Quality method: Coverage verification

After implementation of each stage, measure coverage:

```bash
# Install tarpaulin if not present
cargo install cargo-tarpaulin

# Run coverage on workspace_mounts module
cargo tarpaulin --out Html --timeout 300 --exclude-files 'tests/*'
# Expected: ≥90% line coverage, ≥90% branch coverage for workspace_mounts/

# Review HTML report to identify untested paths
open tarpaulin-report.html  # or your browser
```

If coverage is <90%, investigate untested branches and add tests before proceeding.

### Quality method: Integration testing with real filesystem

For filesystem and permission adapters, test with real filesystem:

```bash
# Create temp directory for tests
TEMP_DIR=$(mktemp -d)
cd $TEMP_DIR

# Create test fixtures
mkdir -p workspaces/valid
ln -s /etc symlink_to_etc
touch readonly.txt && chmod 444 readonly.txt

# Run integration tests
cd /path/to/podbot
cargo test --test integration workspace_mounts -- --nocapture

# Cleanup
rm -rf $TEMP_DIR
```

### Quality method: Rootless testing

For permission validation, test in rootless namespace:

```bash
# Verify podman is available
podman --version

# Run tests in rootless namespace (if available)
podman unshare cargo test --test integration permission -- --nocapture

# Expected: Permission checks succeed in namespace context
# Expected: UID/GID in namespace context match expected values
```

### Quality method: BDD scenario validation

```bash
# Run BDD scenarios
cargo test --test bdd workspace_mounts -- --nocapture

# Expected output includes:
# Scenario: Valid workspace path is validated successfully ... PASSED
# Scenario: Symlink escape attempt is rejected ... PASSED
# Scenario: Path outside allowlist is rejected ... PASSED
# All scenarios pass with correct rejection reasons
```

### Expected outputs

**After Stage 3 (error type)**:
```
running 10 tests
test domain::errors::tests::error_variant_symlink_detected ... ok
test domain::errors::tests::error_variant_not_in_allowlist ... ok
... (8 more variants)
test result: ok. 10 passed; 0 failed
```

**After Stage 4 (filesystem adapter)**:
```
running 25 tests (15 unit + 10 integration)
... filesystem canonicalization tests
... symlink detection tests
... depth limit tests
test result: ok. 25 passed; 0 failed
coverage: 92% (24/26 branches)
```

**After Stage 6 (domain validator)**:
```
running 30 tests (20 unit + 10 property)
test domain::validator::tests::validate_accepts_allowlisted_path ... ok
test domain::validator::tests::validate_rejects_symlink_escape ... ok
test domain::validator::properties::prop_idempotent ... ok
... (27 more)
test result: ok. 30 passed; 0 failed
```

**After Stage 7 (full integration)**:
```
running 50 tests (20 unit + 15 integration + 15 BDD)
test tests::workspace_mounts_tests::* ... ok
test tests::workspace_mounts_integration_tests::* ... ok
running feature: workspace_mounts
Scenario: Valid workspace path is validated successfully ... PASSED
Scenario: Symlink escape attempt is rejected ... PASSED
... (13 more scenarios)
test result: ok. 50 passed; 0 failed
coverage: 92% (lines), 91% (branches)
```

**Final quality gates**:
```bash
$ make check-fmt
All files formatted correctly ✓

$ make lint
No clippy warnings ✓

$ cargo test
... 150+ tests pass (unit + integration + BDD + all other tests) ✓

$ cargo tarpaulin
workspace_mounts: 92% coverage ✓

$ cargo build --release
Build succeeded ✓
```

## Threat model summary

The threat model identifies and mitigates seven major attack vectors:

| # | Attack Vector | Prevention | Test |
|---|---|---|---|
| 1 | **Symlink Escape**: Mount `/tmp/ws/link→/etc` as `/workspace` | Canonicalize; reject symlinks; re-check immediately before mount | `test_symlink_escape_prevented` |
| 2 | **TOCTOU Race**: Symlink added after validation but before mount | Minimize validation window; canonicalize + re-check in atomic operation | `prop_validation_repeatable` |
| 3 | **Symlink Cycle DoS**: `/tmp/ws/a→b/b→a` causes infinite loop during traversal | Limit depth to 40 components; detect visited nodes | `test_symlink_cycle_detected` |
| 4 | **Path Traversal**: `/tmp/ws/../../../../etc/passwd` escapes root | Canonicalization resolves `..` and `.` | `test_path_traversal_prevented` |
| 5 | **Allowlist Prefix Bypass**: `/tmp/workspaces_evil` matches `/tmp/workspaces` | Validate component boundaries: `path.starts_with(root) && (len_match \|\| next_char == '/')` | `test_allowlist_prefix_attack_prevented` |
| 6 | **Permission Escalation**: Mount read-only directory (0o444) as writable | Pre-mount permission check: `(mode & 0o200) != 0` | `test_permission_check_read_only_rejected` |
| 7 | **Rootless UID/GID Mismatch**: Namespace remapping enables escape | Validate UID/GID in actual container namespace using `libc::geteuid()` and `libc::getegid()` | `test_rootless_permission_validation` |

**Out of scope** (delegated to container runtime):
- Kernel bugs (runc/crun) — Mitigation: keep patched
- Mount flag attacks (remount, shared) — Future enhancement
- Capability escalation — Delegated to container runtime LSM/AppArmor

**Assumptions**:
1. Operator configures allowlist roots correctly (no symlinks in config itself)
2. Filesystem is standard POSIX (ext4, tmpfs, overlayfs)
3. Podman/container runtime enforces safe mount flags
4. UID/GID mapping in rootless mode is correct

## Test strategy

### Test pyramid (40+ tests total)

**Unit Tests (25+)** — Domain logic with mocks, no I/O
- Error enum: 10 tests (one per variant)
- Value objects: 5 tests (construction, invariants)
- Canonicalization logic: 5 tests (symlinks, traversal, depth)
- Allowlist: 3 tests (membership, prefix bypass, component boundaries)
- Permission: 2 tests (mode bits, idempotence)
- Domain validator: 5 tests (integration of three checks)

**Property Tests (6+)** — Formal verification of invariants
- Idempotence: `validate(validate(x)) == validate(x)`
- Allowlist consistency: `is_member(x, roots)` always same result
- Canonicalization symmetry: `canonicalize(canonicalize(x)) == canonicalize(x)`
- Error stability: Same invalid path always produces same error
- Depth limit: Paths >40 components always rejected
- Symlink coverage: All symlink scenarios detected

**Integration Tests (10+)** — Real filesystem, no mocks
- Symlink creation and detection
- Symlink cycle creation and handling
- Depth limit enforcement with nested symlinks
- Permission bits validation (read-only files)
- Real config file loading
- Temporary directory cleanup

**BDD Scenarios (8+)** — End-to-end behavior specification
- Valid workspace path accepted
- Symlink escape attempt rejected
- Path outside allowlist rejected
- Read-only directory rejected
- Configuration loading and validation
- Error messages are actionable

**Stress Tests (1+)**
- Validate 1000 paths with various attacks
- Measure canonicalization performance

### Tools & commands

```bash
# Run all tests
cargo test

# Unit tests only (fast, no I/O)
cargo test --lib workspace_mounts

# Integration tests (slower, real filesystem)
cargo test --test integration workspace_mounts

# BDD scenarios
cargo test --test bdd workspace_mounts

# Property tests (proptest crate)
cargo test --lib workspace_mounts -- --test-threads=1 proptest

# Coverage measurement
cargo tarpaulin --out Html --timeout 300

# Coverage targeting specific module
cargo tarpaulin --packages podbot --lib workspace_mounts --out Html
```

### Coverage targets

- **Line coverage**: ≥90% for workspace_mounts module
- **Branch coverage**: ≥90% for workspace_mounts module
- **Error path coverage**: All 10 error variants tested
- **Untested paths**: None (coverage gaps require investigation and new tests)

## Interfaces and dependencies

### New module: `src/workspace_mounts/`

Public API exported from `src/workspace_mounts/lib.rs`:

```rust
// Error type (10 variants, all recoverable)
pub enum WorkspaceMountError { ... }

// Value objects (invariant-enforced)
pub struct ValidatedWorkspacePath { ... }
pub struct AllowedWorkspaceRoot { ... }
pub struct WorkspaceConfig { ... }

// Application service (what consumers use)
pub struct WorkspaceMountServiceImpl { ... }

impl WorkspaceMountServiceImpl {
    pub fn new(config, filesystem, permission, threat_reporter) -> Self { ... }
    pub fn validate_mount(&self, path: &Path) -> Result<PathBuf> { ... }
    pub fn validate_mounts(&self, paths: &[PathBuf]) 
        -> Result<Vec<PathBuf>, Vec<(PathBuf, WorkspaceMountError)>> { ... }
}
```

### Internal module structure

```
src/workspace_mounts/
├── mod.rs                                      # Public exports
├── lib.rs                                      # Application service
├── domain/
│   ├── errors.rs                               # WorkspaceMountError (10 variants)
│   ├── model.rs                                # Value objects
│   ├── validator.rs                            # WorkspacePathValidator (pure logic)
│   ├── ports/
│   │   ├── filesystem.rs                       # FilesystemPort trait
│   │   ├── permission.rs                       # PermissionPort trait
│   │   ├── config.rs                           # WorkspaceConfigPort trait
│   │   └── threat_reporter.rs                  # ThreatReportPort trait
│   └── tests/
│       ├── errors_tests.rs
│       ├── model_tests.rs
│       ├── validator_tests.rs
│       └── properties_tests.rs
│
└── adapters/
    ├── os_filesystem.rs                        # OsFilesystemAdapter (std::fs)
    ├── permission.rs                           # RootlessPermissionAdapter (libc)
    ├── yaml_config.rs                          # YamlConfigAdapter (config loading)
    └── logging.rs                              # LoggingThreatReporter (tracing)
```

### Extension to existing modules

**`src/config.rs`** — Add configuration support:
```rust
pub struct PodobotConfig {
    // ... existing fields
    pub workspace_mounts: Option<WorkspaceMountConfig>,
}

pub struct WorkspaceMountConfig {
    pub allowed_roots: Vec<PathBuf>,
    // future: mount_flags, validation_mode, etc.
}
```

### Dependencies

**Use from Rust standard library**:
- `std::fs` — Filesystem operations
- `std::path` — Path handling
- `std::fs::read_link()` — Symlink detection
- `libc` — POSIX UID/GID checks (likely already dependency)

**Use from existing Podbot dependencies**:
- `thiserror` — Error enum derivation
- `tracing` — Structured logging

**No new external crates** without escalation.

**Testing-only crates** (already available):
- `proptest` — Property testing
- `tempfile` — Temporary test directories
- `mockall` — Mock trait implementations (or `double` if available)
- `insta` — Snapshot testing
- Cucumber/BDD framework if available, else write custom BDD runner

### Type signatures (reference for implementation)

```rust
// Domain errors
#[derive(Debug, Clone, Error)]
pub enum WorkspaceMountError {
    #[error("symlink detected at {path}: {component:?}")]
    SymlinkDetected { path: PathBuf, component: Option<PathBuf> },
    
    #[error("path not in allowlist: {path}\nallowed: {allowed_roots:?}")]
    NotInAllowlist { path: PathBuf, allowed_roots: Vec<PathBuf> },
    
    // ... 8 more variants
}

// Ports (abstractions)
pub trait FilesystemPort {
    fn canonicalize(&self, path: &Path) -> Result<PathBuf>;
    fn has_symlinks(&self, path: &Path) -> Result<bool>;
    fn read_metadata_nofollow(&self, path: &Path) -> Result<std::fs::Metadata>;
    fn exists(&self, path: &Path) -> Result<bool>;
}

pub trait PermissionPort {
    fn is_writable(&self, path: &Path) -> Result<()>;
    fn container_ids(&self) -> Result<(u32, u32)>;
    fn check_security_context(&self, path: &Path) -> Result<()>;
}

// Domain service (pure logic)
pub struct WorkspacePathValidator {
    config: WorkspaceConfig,
    filesystem: Arc<dyn FilesystemPort>,
    permission: Arc<dyn PermissionPort>,
    threat_reporter: Arc<dyn ThreatReportPort>,
}

impl WorkspacePathValidator {
    pub fn validate(&self, path: &Path) -> Result<ValidatedWorkspacePath>;
}

// Application service (what callers use)
pub struct WorkspaceMountServiceImpl {
    validator: Arc<WorkspacePathValidator>,
}

impl WorkspaceMountServiceImpl {
    pub fn validate_mount(&self, path: &Path) -> Result<PathBuf>;
    pub fn validate_mounts(&self, paths: &[PathBuf]) 
        -> Result<Vec<PathBuf>, Vec<(PathBuf, WorkspaceMountError)>>;
}
```

## Integration with other tasks

This task depends on and integrates with:

- **Task 1.4.1 (Container Launch)**: The WorkspaceMountServiceImpl validates all workspace paths before container command construction. Canonical paths are passed to bind_mount().

- **Task 2.2.5 (MCP Integration)**: The same service is used by MCP workspace mount protocol handler. Ensures consistent validation across all launch entry points.

**Data flow**:
```
HTTP/CLI/MCP request
    ↓
WorkspaceMountServiceImpl::validate_mount()
    ↓
WorkspacePathValidator (pure domain logic)
    ├─→ Canonicalize (FilesystemPort)
    ├─→ Check allowlist (pure logic)
    └─→ Validate permissions (PermissionPort)
    ↓
ValidatedWorkspacePath (newtype, invariant-enforced)
    ↓
Container launch (Task 1.4.1) or MCP response (Task 2.2.5)
```

Both tasks use **identical validation**, preventing validation bypass via different launch paths.

## Concrete steps for implementation

### Setup and environment

```bash
# Ensure you're on the correct branch
git branch --show-current
# Expected: 4-2-2-safe-host-mounted-workspaces

# Verify Rust toolchain
rustc --version
cargo --version
# Expected: stable or recent nightly

# Install tarpaulin for coverage (if not present)
cargo install cargo-tarpaulin

# Create temp directory for test fixtures
export TEST_FIXTURES_DIR=$(mktemp -d)
echo "Test fixtures will be in: $TEST_FIXTURES_DIR"
```

### Stage-by-stage implementation

Each stage proceeds through Red-Green-Refactor, followed by validation:

```bash
# After completing each stage, run:
cargo check                      # Compilation check
cargo test --lib               # Unit tests
cargo test --test integration  # Integration tests
make check-fmt                 # Format check
make lint                       # Lint check

# If all pass, proceed to next stage.
# If any fails, stop and fix before proceeding.
```

### Coverage measurement at each stage

```bash
# After Stage 4 (filesystem adapter) and beyond:
cargo tarpaulin --out Html --timeout 300 --exclude-files 'tests/*'
# Expected: ≥85% coverage (will increase as more stages complete)

# Final coverage check (Stage 9):
cargo tarpaulin --out Html --timeout 300 --exclude-files 'tests/*'
# Expected: ≥90% line coverage, ≥90% branch coverage
```

### PR creation (final step)

```bash
# After all stages complete and quality gates pass:
git log --oneline -10
# Verify commits are descriptive (one per stage)

git push -u origin 4-2-2-safe-host-mounted-workspaces

# Create PR (use gh or GitHub web interface)
gh pr create --title "Implement safe host-mounted workspaces (task 4.2.2)" \
  --body "Closes #<issue_number>
  
## Summary
- Implement WorkspaceMountService with hexagonal architecture
- Add validation for canonicalization, allowlist, permissions
- Add 40+ tests with ≥90% coverage
- Document threat model and configuration

## Validation
- All quality gates pass (fmt, lint, test)
- Coverage ≥90% line and branch
- BDD scenarios pass
- Documentation updated"

# Expected: PR created and CI checks run
```

## Idempotence and recovery

All stages are idempotent:

- **Tests are idempotent**: Run `cargo test` multiple times, expect same results
- **Code changes are additive**: Each stage adds new modules or functions; no destructive refactoring of existing code
- **Configuration is immutable at runtime**: Once loaded, config is never modified
- **Test fixtures are cleaned up**: Use `tempfile::TempDir` to auto-cleanup

**If a stage fails partway through**:

1. Identify which step failed (read error message)
2. Fix the issue (e.g., add missing import, fix logic)
3. Rerun the stage's validation from the beginning
4. Do not skip steps or skip commits

**If a test fails repeatedly** (3+ attempts):

1. Stop and escalate (per Tolerances)
2. Document the issue in `Decision Log`
3. Propose a path forward with trade-offs

## Artifacts and notes

### Key design decisions

1. **Hexagonal architecture** — Isolates domain logic from I/O; enables testing without infrastructure; simplifies future enhancements (e.g., SELinux checks)

2. **Canonicalization always rejects symlinks** — Stricter security posture; no symlinks in validated paths ever

3. **Allowlist is configuration-based** — Operators control explicitly via YAML; validated at startup (fail-fast)

4. **Permission validation is strict** — Read-only directories are rejected; prevents mount-time failures

5. **Error type is exhaustive** — 10 variants, each with actionable context; all errors are domain-owned

6. **Three-stage validation** — Canonicalize → Allowlist → Permissions; each stage is independent and testable

7. **Shared service across launch paths** — Same WorkspaceMountServiceImpl used by HTTP, CLI, MCP; prevents validation bypass

### Example configuration

```yaml
podbot:
  workspace_mounts:
    allowed_roots:
      - /tmp/workspaces
      - /home/ci/work
      - /var/lib/podbot/volumes
```

### Example error flow

**Invalid path: symlink escape**
```rust
// Input: /tmp/workspaces/link → /etc
validate_mount("/tmp/workspaces/link")
    ↓
canonicalize() → detected symlink at "link"
    ↓
Error::SymlinkDetected { path, component: Some("link") }
    ↓
ThreatReporter::report_mount_rejection()
    ↓
Response to operator: "Mount rejected: symlink detected at /tmp/workspaces/link (component: link)"
```

**Invalid path: outside allowlist**
```rust
// Input: /home/attacker/workspace
// Config: allowed_roots: [/tmp/workspaces]
validate_mount("/home/attacker/workspace")
    ↓
canonicalize() → Ok(/home/attacker/workspace)
    ↓
check_in_allowlist() → not a member
    ↓
Error::NotInAllowlist { path, allowed_roots: [/tmp/workspaces] }
    ↓
Response: "Mount rejected: /home/attacker/workspace not in allowlist [/tmp/workspaces]"
```

---

## Revision note

(None yet; to be updated if the plan is revised during implementation.)
