# 4.2.2 Implement Safe Host-Mounted Workspaces

This ExecPlan is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date
as work proceeds.

Status: DRAFT

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

## Constraints

Hard invariants that must hold throughout implementation.

- No public API changes in the workspace launch interface
- No new external dependencies without approval
- Rootless engine compatibility (podman, Docker)
- Backward compatibility with existing workspace configurations
- No symlinks in the validated set after canonicalization

## Tolerances (exception triggers)

Thresholds that trigger escalation when breached.

- Scope: ≤15 files or ≤2000 net lines of code added
- Dependencies: No new crates without escalation
- Interface changes: Stop if existing signatures must change
- Test coverage: ≥90% for canonicalization and allowlist validation
- Iterations: Stop if same test fails 3+ consecutive times
- Design decisions: Escalate if threat model interpretation differs

## Risks

Known uncertainties that might affect the plan.

- TOCTOU races between canonicalization and mount time
  Mitigation: Use `O_NOFOLLOW` flag and atomic operations
- Permission validation in rootless: UID/GID remapping makes checks
  unreliable
  Mitigation: Test in actual container namespace using `podman unshare`
- Symlink cycles could cause DoS during canonicalization
  Mitigation: Detect cycles and limit depth to 40 components
- Configuration ambiguity in allowlist roots
  Mitigation: Use structured YAML/TOML with startup validation
- Mount flag escapes via different mount options
  Mitigation: Reference CVE-2025 runc vulnerabilities

## Progress

Use checkboxes to track granular steps with timestamps.

- [ ] **Phase 1: Threat model documentation**
  - [ ] (2026-06-18) Review threat model with attack scenarios
  - [ ] (TBD) Define allowlist configuration schema
  - [ ] (TBD) Write threat model section in docs/users-guide.md

- [ ] **Phase 2: Path canonicalization (Red-Green-Refactor)**
  - [ ] (TBD) Add failing unit tests for canonicalization
  - [ ] (TBD) Implement `canonicalize_workspace_path()`
  - [ ] (TBD) Refactor and verify all tests pass

- [ ] **Phase 3: Allowlist enforcement (Red-Green-Refactor)**
  - [ ] (TBD) Add failing tests for allowlist membership
  - [ ] (TBD) Implement `enforce_allowlist()`
  - [ ] (TBD) Refactor and verify all tests pass

- [ ] **Phase 4: Permission validation (Red-Green-Refactor)**
  - [ ] (TBD) Add failing tests for write-permission validation
  - [ ] (TBD) Implement `validate_mount_permissions()`
  - [ ] (TBD) Add integration tests with rootless podman

- [ ] **Phase 5: Integration and edge cases**
  - [ ] (TBD) Write BDD scenarios for happy and sad paths
  - [ ] (TBD) Integrate three functions into `mount_workspace()`
  - [ ] (TBD) Add snapshot and property tests

- [ ] **Phase 6: Documentation and quality gates**
  - [ ] (TBD) Update docs/users-guide.md
  - [ ] (TBD) Update docs/developers-guide.md
  - [ ] (TBD) Run make check-fmt, make lint, make test
  - [ ] (TBD) Run coderabbit review --agent and resolve concerns

- [ ] **Phase 7: Roadmap update**
  - [ ] (TBD) Mark task 4.2.2 as "done" in roadmap
  - [ ] (TBD) Push branch and create draft PR

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

Podbot is organized around composing container launches from reusable
request/plan primitives. The codebase currently lacks specialized
workspace mount validation. Related infrastructure exists for cargo
utilities, mount inspection, and sandbox infrastructure.

### Implementation structure

New module `workspace_mounts` with three core functions for path
canonicalization, allowlist enforcement, and permission validation.

### Key files to create/modify

- `src/workspace_mounts.rs` — New module
- `src/config.rs` — Add `WorkspaceMountConfig` struct
- `tests/workspace_mounts_tests.rs` — Unit tests
- `tests/bdd/workspace_mounts.feature` — BDD scenarios
- `docs/users-guide.md` — Usage documentation
- `docs/developers-guide.md` — Component architecture

## Plan of work

Implementation proceeds through seven stages with clear validation
points. All code follows Red-Green-Refactor.

### Stage A: Design & threat model documentation

1. Create threat model document
2. Define configuration schema
3. Validation: Threat model is clear, schema is unambiguous

### Stage B: Core path canonicalization

Implement `canonicalize_workspace_path()` with Red-Green-Refactor:
failing tests, minimal implementation, refactor.

### Stage C: Allowlist enforcement

Implement `enforce_allowlist()` with Red-Green-Refactor.

### Stage D: Permission validation

Implement `validate_mount_permissions()` with Red-Green-Refactor
including integration tests with rootless podman.

### Stage E: Integration and edge cases

Write BDD scenarios and integrate three functions into single
`mount_workspace()` function. Add snapshot and property tests.

### Stage F: Documentation and validation

Update documentation and run quality gates.

### Stage G: Roadmap update and PR

Update roadmap marking 4.2.2 as "done" and create draft PR.

## Validation and acceptance

### Acceptance criteria

1. Unit tests pass with ≥90% coverage
2. BDD scenarios pass with correct error reasons
3. Configuration integration in `src/config.rs`
4. Documentation complete in users-guide and developers-guide
5. All quality gates pass (fmt, lint, test, CodeRabbit)
6. Roadmap updated
7. PR created with execplan and lody session links

## Interfaces and dependencies

### New module: `src/workspace_mounts.rs`

Public interface with enum `WorkspaceMountError` and four functions:

- `canonicalize_workspace_path()`
- `enforce_allowlist()`
- `validate_mount_permissions()`
- `mount_workspace()`

### Extension to `src/config.rs`

Add `WorkspaceMountConfig` struct with `allowed_roots: Vec<PathBuf>`.

### Dependencies

All from Rust standard library. No external crates without escalation.

## Artifacts and notes

### Key design decisions

1. Canonicalization always rejects symlinks: stricter, more secure
2. Allowlist is configuration-based: operators control explicitly
3. Permission validation is strict: read-only directories rejected
4. Error messages are actionable: help operators understand failures

### Example configuration

```yaml
podbot:
  workspace_mounts:
    allowed_roots:
      - /tmp/workspaces
      - /home/ci/work
```

---

## Revision note

(None yet; to be updated if the plan is revised.)
