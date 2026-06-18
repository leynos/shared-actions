# 4.2.2 Implement Safe Host-Mounted Workspaces

This ExecPlan (execution plan) is a living document. The sections `Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: DRAFT


## Purpose / big picture

Podbot normalizes every container launch into typed request and plan values, creating a shared control plane for interactive sessions, hosted protocols, MCP wires, and orchestration surfaces. This milestone implements safe host-mounted workspaces—the mechanism by which operators can mount host directories into container sessions while maintaining strict boundaries against symlink escapes, unauthorized path access, and privilege escalation vectors.

After this change, users will be able to configure host-mounted workspaces with confidence that:

1. All mount paths are canonicalized; symlinks and path traversal attacks are rejected
2. Mounts are confined to an allowlisted set of root directories configured by the operator
3. Write permissions are validated before mounting (accounting for rootless-engine scenarios)
4. The threat model and security boundary are documented clearly
5. Negative coverage tests prove forbidden paths are rejected even under adversarial conditions

Operators enable this by configuring workspace mount roots in Podbot's configuration, then specifying workspace paths during launch. The system validates paths against the allowlist, canonicalizes them, and rejects any attempt to escape the allowlisted roots. A new section in `docs/users-guide.md` documents threat boundaries and safe usage patterns.


## Constraints

Hard invariants that must hold throughout implementation.

- **No public API changes**: The workspace launch interface must remain stable. If a new type or function is required, it must not change existing function signatures or trait interfaces in the launching code path.
- **No new external dependencies without approval**: The implementation must use Rust stable library crates. If `path-security`, `soft-canonicalize`, or similar crates are required, document the decision in the Decision Log and escalate.
- **Rootless engine compatibility**: The implementation must work correctly in both rootless podman and Docker environments. The permission validation logic must account for user namespace remapping (UID/GID shifts).
- **Backward compatibility**: Existing workspace configurations without explicit mount roots must continue to work. If a migration is needed, it must be invisible to operators (auto-populated from safe defaults).
- **No symlinks in the validated set**: After canonicalization, if a mount path contains a symlink in any component, it must be rejected. This prevents time-of-check-time-of-use (TOCTOU) races and symlink-exchange attacks.


## Tolerances (exception triggers)

Thresholds that trigger escalation when breached.

- **Scope**: If implementation requires changes to more than 15 files or more than 2000 net lines of code added, stop and escalate.
- **Dependencies**: If a new external dependency (crate) is required beyond Rust stdlib, stop and escalate with rationale.
- **Interface changes**: If existing public function signatures in the workspace launch path must change, stop and escalate with justification.
- **Test coverage**: If unit test coverage for canonicalization and allowlist validation falls below 90%, stop and rework tests before proceeding.
- **Iterations**: If the same test fails more than 3 consecutive times after fixes, stop and escalate with root-cause analysis.
- **Design decisions**: If the threat model or security boundary interpretation differs from the plan, document in Decision Log and escalate for approval.


## Risks

Known uncertainties that might affect the plan. Each risk includes severity, likelihood, and mitigation.

- **Risk**: TOCTOU races between canonicalization and mount time. An attacker could replace a validated path with a symlink between validation and actual mount invocation.
  **Severity**: High  
  **Likelihood**: Medium  
  **Mitigation**: Use `O_NOFOLLOW` flag and atomic operations (`openat2` with `RESOLVE_IN_ROOT` when available). Validate that no code path leaves a window between checks and mount. Include explicit TOCTOU test cases.

- **Risk**: User namespace remapping makes permission checks unreliable in rootless scenarios. A path writable by the rootless engine may not be writable by the container's remapped UIDs.
  **Severity**: High  
  **Likelihood**: Medium  
  **Mitigation**: Test permission validation in both rootless and root-privileged environments. Use `podman unshare` to validate permissions in the correct namespace. Document the UID/GID remapping assumptions in the threat model.

- **Risk**: Symlink cycles or deeply nested symlinks could cause denial-of-service or resource exhaustion during canonicalization.
  **Severity**: Medium  
  **Likelihood**: Low  
  **Mitigation**: Implement symlink cycle detection. Limit canonicalization depth (e.g., max 40 components). Return clear error messages on depth exceeded.

- **Risk**: The allowlist configuration format may be ambiguous or error-prone. Operators could misconfigure roots and accidentally deny legitimate mounts or allow unintended paths.
  **Severity**: Medium  
  **Likelihood**: Low  
  **Mitigation**: Use structured configuration (YAML/TOML, not free-form strings). Validate configuration at startup and report errors. Include examples and warnings in documentation.

- **Risk**: Interactions with different mount flags (`bind`, `rbind`, `nosuid`, `noexec`, etc.) may expose mount escape vectors not anticipated in the threat model.
  **Severity**: Medium  
  **Likelihood**: Low  
  **Mitigation**: Document which mount flags are safe and which are dangerous. Validate mount options against a safe list. Reference CVE-2025 series runc vulnerabilities to ensure mitigations are specific.


## Progress

Use a list with checkboxes to track granular steps. Every milestone is documented with a timestamp.

- [ ] **Phase 1: Design & threat model documentation (Milestone A)**
  - [ ] (2026-06-18) Review and document threat model with specific attack scenarios.
  - [ ] (TBD) Define allowlist configuration schema and default safe roots.
  - [ ] (TBD) Write threat model section in docs/users-guide.md.

- [ ] **Phase 2: Core path canonicalization (Milestone B)**
  - [ ] (TBD) Add unit tests for path canonicalization (Red: tests fail before implementation).
  - [ ] (TBD) Implement `canonicalize_workspace_path()` function with symlink detection.
  - [ ] (TBD) Tests pass; refactor for clarity and performance (Green/Refactor).

- [ ] **Phase 3: Allowlist enforcement (Milestone C)**
  - [ ] (TBD) Add configuration struct for mount roots (allow-listed directories).
  - [ ] (TBD) Add unit tests for allowlist membership checking (Red).
  - [ ] (TBD) Implement `enforce_allowlist()` function.
  - [ ] (TBD) Tests pass and refactor (Green/Refactor).

- [ ] **Phase 4: Permission validation (Milestone D)**
  - [ ] (TBD) Add unit tests for write-permission validation in both rootless and root contexts (Red).
  - [ ] (TBD) Implement `validate_mount_permissions()` with namespace handling.
  - [ ] (TBD) Tests pass; add integration tests with `testcontainers-rs` for rootless podman (Green/Refactor).

- [ ] **Phase 5: Integration and edge cases (Milestone E)**
  - [ ] (TBD) Write BDD scenarios for happy path and forbidden paths (Red).
  - [ ] (TBD) Integrate canonicalization, allowlist, and permission checks into workspace launch.
  - [ ] (TBD) BDD scenarios pass (Green/Refactor).
  - [ ] (TBD) Add snapshot tests for error messages and mount rejection reasons.

- [ ] **Phase 6: Documentation and validation (Milestone F)**
  - [ ] (TBD) Update docs/users-guide.md with workspace mount section.
  - [ ] (TBD) Update docs/developers-guide.md with component architecture.
  - [ ] (TBD) Run `make check-fmt`, `make lint`, `make test` and confirm all pass.
  - [ ] (TBD) Run `coderabbit review --agent` and resolve all concerns.

- [ ] **Phase 7: Roadmap update (Milestone G)**
  - [ ] (TBD) Mark task 4.2.2 as "done" in docs/podbot-roadmap.md.
  - [ ] (TBD) Push branch and create draft PR with execplan summary.


## Surprises & discoveries

Unexpected findings during implementation. This section is updated as work proceeds.

(None yet; will be populated during implementation.)


## Decision log

Record every significant decision made while working on the plan.

- **Decision**: Use Rust `std::fs::canonicalize()` as the baseline for path normalization, with additional checks for symlink presence in the canonical path.  
  **Rationale**: Rust stdlib is stable and audited. If symlinks are present after canonicalization, it indicates a TOCTOU issue or misconfiguration that should be rejected. This avoids introducing external path-security crates unless we encounter a genuine limitation.  
  **Date/Author**: 2026-06-18 (planning phase).

- **Decision**: Allowlist roots are configured in Podbot configuration (YAML) at startup, not per-request.  
  **Rationale**: This enforces operator control and prevents operators from accidentally allowing arbitrary mounts. Operators must explicitly configure safe roots; Podbot cannot infer them dynamically. Configuration errors are detected at startup, not at mount time.  
  **Date/Author**: 2026-06-18 (planning phase).

- **Decision**: Permission validation will use `podman unshare` in rootless scenarios to test permissions in the container's namespace.  
  **Rationale**: UID/GID remapping in rootless engines makes naive permission checks unreliable. Testing in the actual namespace where the mount will occur is the only reliable method.  
  **Date/Author**: 2026-06-18 (planning phase).

(Additional decisions will be recorded during implementation.)


## Outcomes & retrospective

This section is populated at major milestones and at completion.

(To be updated as work proceeds.)


## Context and orientation

### Current state

Podbot is a Rust project organized around composing container launches from reusable request/plan primitives. The codebase currently lacks specialized workspace mount validation. Related infrastructure exists:

- **Existing cargo utilities** (`cargo_utils.py`): Workspace root discovery by walking up the directory tree looking for `Cargo.toml` with `[workspace]` table.
- **Existing mount inspection** (`validate_cli.py`): Mount filesystem type and executable-store detection via `/proc/self/mountinfo`.
- **Existing sandbox infrastructure** (`validate_polythene.py`): Context manager pattern for managing container sessions with configurable isolation strategies.

The implementation will add a new module, `workspace_mounts`, with three core functions:

1. `canonicalize_workspace_path(path: &Path) -> Result<PathBuf, Error>` — Validate and canonicalize a proposed mount path.
2. `enforce_allowlist(canonical_path: &Path, allowed_roots: &[PathBuf]) -> Result<(), Error>` — Verify the path is within the allowlisted roots.
3. `validate_mount_permissions(path: &Path, container_uid: u32, container_gid: u32) -> Result<(), Error>` — Ensure the path is writable by the container.

### Key files and modules

- `src/workspace_mounts.rs` — New module for path validation and allowlist enforcement.
- `src/config.rs` (existing) — To be extended with `WorkspaceMountConfig` struct.
- `tests/workspace_mounts_tests.rs` — Unit tests for all three functions.
- `tests/bdd/workspace_mounts.feature` (new) — BDD scenarios for happy and unhappy paths.
- `docs/users-guide.md` — To add section on configuring and using host-mounted workspaces.
- `docs/developers-guide.md` — To add component architecture section for workspace mounts.


## Plan of work

The implementation proceeds through seven stages, each with clear go/no-go validation points. All code changes follow Red-Green-Refactor: add a failing test, implement the minimal fix, then refactor.

### Stage A: Design & threat model documentation

Before writing code, document the threat model and design decisions.

1. Create a new document `docs/adr/ADR-0003-host-mounted-workspace-security.md` (or use the component architecture section of developers-guide.md).
2. Document the following threat model sections:
   - **Attacks prevented**: Symlink escapes (including symlink-exchange), path traversal, UID 0 escalation via mount options.
   - **Attacks out-of-scope**: Attacks that exploit kernel bugs in the container runtime itself (e.g., CVE-2025 runc vulnerabilities). Mitigation is to keep runc and podman patched.
   - **Assumptions**: Operator correctly configures allowlisted roots. Filesystem is ext4/tmpfs/overlayfs (no btrfs reflexivity). Container UID/GID mapping is correct.

3. Define the configuration schema for allowlisted roots. Example:
   ```yaml
   workspace_mounts:
     allowed_roots:
       - /tmp/workspaces
       - /home/ci/work
     safe_defaults:
       - /tmp
   ```

4. Write validation criteria: "The threat model document clearly identifies attack vectors, assumptions, and out-of-scope cases. Configuration schema is unambiguous."

**Validation**: Review threat model doc and configuration schema. Escalate if any attack vector is unclear or assumptions are unrealistic.

### Stage B: Core path canonicalization

Implement path canonicalization and symlink detection as the foundation.

#### Red phase (failing tests)

1. Create `tests/workspace_mounts_tests.rs` with the following test cases:
   ```
   - test_canonicalize_simple_path: /tmp/work → /tmp/work (no symlinks, no .. or .)
   - test_canonicalize_with_dotdot: /tmp/work/../work → /tmp/work
   - test_canonicalize_with_dot: /tmp/work/. → /tmp/work
   - test_canonicalize_symlink_in_path: /tmp/link_to_work/file (where link_to_work → work) → ERROR
   - test_canonicalize_symlink_chain: /tmp/a/b/c where b → ../other/path → ERROR
   - test_canonicalize_nonexistent_path: /tmp/nonexistent/dir → ERROR (or allow if soft-canonicalization)
   - test_canonicalize_cycle: /tmp/a → /tmp/a → ERROR (symlink cycle detection)
   - test_canonicalize_deep_nesting: path with 50+ components → ERROR (DoS prevention)
   ```

2. Run tests; expect all to fail with "function not found" or similar.

#### Green phase (minimal implementation)

1. Create `src/workspace_mounts.rs`:
   ```rust
   use std::fs;
   use std::path::{Path, PathBuf};
   
   #[derive(Debug)]
   pub enum WorkspaceMountError {
       SymlinkDetected(PathBuf),
       SymlinkCycle(PathBuf),
       DepthExceeded,
       PermissionDenied,
       NotFound,
   }
   
   pub fn canonicalize_workspace_path(path: &Path) -> Result<PathBuf, WorkspaceMountError> {
       let canonical = fs::canonicalize(path)
           .map_err(|e| match e.kind() {
               std::io::ErrorKind::NotFound => WorkspaceMountError::NotFound,
               std::io::ErrorKind::PermissionDenied => WorkspaceMountError::PermissionDenied,
               _ => WorkspaceMountError::NotFound,
           })?;
       
       // Check for symlinks in the canonical path by comparing readlink results.
       // If any component resolves via symlink, reject.
       validate_no_symlinks(&canonical)?;
       
       Ok(canonical)
   }
   
   fn validate_no_symlinks(path: &Path) -> Result<(), WorkspaceMountError> {
       let mut components = path.components();
       let mut current = PathBuf::from("/");
       let mut depth = 0;
       const MAX_DEPTH: usize = 40;
       
       while let Some(component) = components.next() {
           depth += 1;
           if depth > MAX_DEPTH {
               return Err(WorkspaceMountError::DepthExceeded);
           }
           
           current.push(component);
           
           // If reading the link succeeds, a symlink exists.
           if fs::read_link(&current).is_ok() {
               return Err(WorkspaceMountError::SymlinkDetected(current.clone()));
           }
       }
       
       Ok(())
   }
   ```

2. Run the failing tests; they should now pass or fail for the expected reasons (e.g., "SymlinkDetected" for symlink tests).

#### Refactor phase

1. Improve error handling to match test expectations exactly.
2. Add better error messages (include path in error variant).
3. Run tests again; all should pass.
4. Run `make test` to confirm no regressions.

### Stage C: Allowlist enforcement

Implement allowlist membership checking.

#### Red phase

1. Add tests to `tests/workspace_mounts_tests.rs`:
   ```
   - test_enforce_allowlist_within_root: /tmp/workspaces/job123 against [/tmp/workspaces] → OK
   - test_enforce_allowlist_outside_root: /var/work against [/tmp/workspaces] → ERROR
   - test_enforce_allowlist_escape_attempt: /tmp/workspaces/../../../etc against [/tmp/workspaces] → ERROR
   - test_enforce_allowlist_multiple_roots: /home/ci/job against [/tmp, /home/ci] → OK
   - test_enforce_allowlist_empty_roots: /tmp/work against [] → ERROR
   - test_enforce_allowlist_prefix_attack: /tmp/workspaces_other against [/tmp/workspaces] → ERROR (name-based bypass)
   ```

2. Run tests; expect failures.

#### Green phase

1. Add to `src/workspace_mounts.rs`:
   ```rust
   pub fn enforce_allowlist(
       canonical_path: &Path,
       allowed_roots: &[PathBuf],
   ) -> Result<(), WorkspaceMountError> {
       if allowed_roots.is_empty() {
           return Err(WorkspaceMountError::NoAllowedRoots);
       }
       
       for root in allowed_roots {
           // Check if path starts with root and the next component is a separator or end-of-path.
           if canonical_path.starts_with(root) {
               // Prevent "/tmp/workspaces_other" from matching "/tmp/workspaces".
               if canonical_path.parent() == Some(root) || root.parent().map_or(false, |p| canonical_path.starts_with(p)) {
                   return Ok(());
               }
           }
       }
       
       Err(WorkspaceMountError::NotInAllowlist(canonical_path.to_path_buf()))
   }
   ```

2. Run tests; refine error handling until all pass.

#### Refactor

1. Extract path prefix-checking logic into a helper function.
2. Run tests again; all should pass.
3. Run `make test`.

### Stage D: Permission validation

Implement write-permission checking with rootless-engine support.

#### Red phase

1. Add tests:
   ```
   - test_validate_permissions_writable_dir: /tmp/writable (mode 0755) → OK
   - test_validate_permissions_read_only_dir: /tmp/readonly (mode 0555) → ERROR
   - test_validate_permissions_nonexistent_dir: /tmp/nonexistent → ERROR
   - test_validate_permissions_rootless_namespace_check: (in rootless podman context) → OK
   - test_validate_permissions_permission_denied: /root/private (not accessible) → ERROR
   ```

2. For rootless tests, use `testcontainers-rs` with a rootless podman fixture. (Skippable if podman not available.)

3. Run tests; expect failures.

#### Green phase

1. Add to `src/workspace_mounts.rs`:
   ```rust
   pub fn validate_mount_permissions(
       path: &Path,
       container_uid: u32,
       container_gid: u32,
   ) -> Result<(), WorkspaceMountError> {
       let metadata = fs::metadata(path)
           .map_err(|e| match e.kind() {
               std::io::ErrorKind::NotFound => WorkspaceMountError::NotFound,
               std::io::ErrorKind::PermissionDenied => WorkspaceMountError::PermissionDenied,
               _ => WorkspaceMountError::PermissionDenied,
           })?;
       
       if !metadata.is_dir() {
           return Err(WorkspaceMountError::NotADirectory);
       }
       
       // Check write bit for owner.
       let mode = metadata.permissions().mode();
       if (mode & 0o200) == 0 {
           return Err(WorkspaceMountError::NotWritable);
       }
       
       Ok(())
   }
   ```

2. Run tests. For rootless scenarios, tests may need conditional logic (skipped on non-rootless systems, run with `podman unshare` on rootless).

#### Refactor

1. Extract permission-checking logic and add more nuanced checks (owner vs. group vs. other).
2. Run tests; all should pass.
3. Run `make test`.

### Stage E: Integration and edge cases

Integrate the three functions and test end-to-end behaviors with BDD.

#### Red phase (BDD scenarios)

1. Create `tests/bdd/workspace_mounts.feature`:
   ```gherkin
   Feature: Safe host-mounted workspaces
     Scenario: Mount a workspace within the allowlist
       Given a configured allowed root "/tmp/workspaces"
       And a workspace path "/tmp/workspaces/my-job"
       When the workspace is mounted
       Then the mount succeeds
     
     Scenario: Reject workspace outside the allowlist
       Given a configured allowed root "/tmp/workspaces"
       And a workspace path "/var/work"
       When the workspace is mounted
       Then the mount fails with reason "NotInAllowlist"
     
     Scenario: Reject symlink escapes
       Given a configured allowed root "/tmp/workspaces"
       And a symlink at "/tmp/workspaces/link" pointing to "/etc"
       When the workspace is mounted to the symlink
       Then the mount fails with reason "SymlinkDetected"
     
     Scenario: Reject paths with symlinks in any component
       Given a configured allowed root "/tmp/workspaces"
       And a symlink at "/tmp/workspaces/a/b" (where /tmp/workspaces/a is a symlink)
       When the workspace is mounted to "/tmp/workspaces/a/b/c"
       Then the mount fails with reason "SymlinkDetected"
   ```

2. Run BDD scenarios; expect failures (steps not implemented).

#### Green phase

1. Implement BDD step definitions (using `cucumber` or equivalent BDD framework).
2. Integrate the three functions into a single `mount_workspace()` function:
   ```rust
   pub fn mount_workspace(
       path: &Path,
       allowed_roots: &[PathBuf],
       container_uid: u32,
       container_gid: u32,
   ) -> Result<PathBuf, WorkspaceMountError> {
       let canonical = canonicalize_workspace_path(path)?;
       enforce_allowlist(&canonical, allowed_roots)?;
       validate_mount_permissions(&canonical, container_uid, container_gid)?;
       Ok(canonical)
   }
   ```

3. Hook BDD steps to call this function and assert outcomes.

4. Run BDD scenarios; they should pass.

#### Refactor

1. Add snapshot tests for error messages (using `insta` crate).
2. Add property tests (using `proptest`) for path canonicalization: ensure that for any valid path and allowlist, the result is deterministic and doesn't change on repeated calls.
3. Run `make test` and confirm all pass.

### Stage F: Documentation and validation

Document the feature and run all quality gates.

1. **Update `docs/users-guide.md`**:
   - Add section "Configuring Host-Mounted Workspaces"
   - Explain the threat model: symlink escapes are prevented, allowlists are enforced, permissions are validated.
   - Provide example configuration in YAML.
   - Show example command to launch a workspace: `podbot launch --workspace /tmp/workspaces/my-job`.
   - Document the error messages and how to interpret them.

2. **Update `docs/developers-guide.md`**:
   - Add section "Workspace Mounts Component Architecture"
   - Describe the three-function design and why it's organized that way.
   - Explain the threat model assumptions.
   - Reference the ADR if one exists.

3. **Run quality gates**:
   ```bash
   make check-fmt
   make lint
   make test
   coderabbit review --agent
   ```

4. **Review and resolve CodeRabbit findings** (described in next milestone).

### Stage G: Roadmap update and PR

1. Update `docs/podbot-roadmap.md` (or create it if it doesn't exist):
   - Mark task 4.2.2 as "done" with completion date.

2. Push the branch:
   ```bash
   git push origin 4-2-2-safe-host-mounted-workspaces
   ```

3. Create a draft PR with the title:
   ```
   (4.2.2) Implement safe host-mounted workspaces
   ```

4. Include in the PR body:
   - Summary of what was implemented.
   - Threat model summary (symlink escapes prevented, allowlists enforced).
   - Link to execplan: `docs/execplans/4-2-2-safe-host-mounted-workspaces.md`
   - Link to lody session: `https://lody.ai/leynos/sessions/${LODY_SESSION_ID}`


## Concrete steps

### Initial setup (already completed)

1. Rename branch:
   ```bash
   git branch -m 4-2-2-safe-host-mounted-workspaces
   ```

2. Create leta workspace:
   ```bash
   leta workspace add .
   ```

### Phase 1: Threat model documentation

1. Create threat model document (or ADR):
   ```bash
   touch docs/adr/ADR-0003-host-mounted-workspace-security.md
   ```

2. Document threat model, assumptions, and configuration schema.

3. Validate: Threat model is clear, configuration schema is unambiguous.

### Phases 2–5: Implementation (Red-Green-Refactor per stage)

Run after completing each stage:

```bash
cargo test workspace_mounts
cargo fmt --check
cargo clippy
make test
```

### Phase 6: Quality gates

```bash
make check-fmt
make lint
make test
coderabbit review --agent
```

### Phase 7: Roadmap and PR

1. Update roadmap and push:
   ```bash
   git add -A
   git commit -m "docs: mark 4.2.2 as complete"
   git push origin 4-2-2-safe-host-mounted-workspaces
   ```

2. Get `LODY_SESSION_ID`:
   ```bash
   echo ${LODY_SESSION_ID}
   ```

3. Create draft PR with title and lody session link in description.


## Validation and acceptance

### Acceptance criteria

All of the following must be true at completion:

1. **Unit tests pass**: `cargo test workspace_mounts --lib` completes with all tests passing.
   - Canonicalization tests cover symlinks, cycles, depth limits.
   - Allowlist tests cover inclusion, exclusion, prefix attacks.
   - Permission tests cover writable, read-only, nonexistent paths.
   - Coverage ≥ 90% for `workspace_mounts.rs`.

2. **BDD scenarios pass**: `cargo test --test bdd_workspace_mounts` completes with all scenarios passing.
   - Happy path: valid workspace within allowlist mounts successfully.
   - Sad paths: symlinks, out-of-bounds paths, and permission errors are rejected with correct error reasons.

3. **Integration with configuration**: Configuration struct in `src/config.rs` includes `workspace_mount_roots: Vec<PathBuf>`.
   - Configuration is validated at startup; invalid roots are reported as errors.
   - Default configuration includes `/tmp` as a safe root (if appropriate).

4. **Documentation is complete**:
   - `docs/users-guide.md` includes section on configuring and using host-mounted workspaces.
   - `docs/developers-guide.md` includes component architecture section.
   - Threat model is documented (in ADR or developers-guide).

5. **Quality gates pass**:
   - `make check-fmt` passes (no formatting issues).
   - `make lint` passes (no clippy warnings in new code).
   - `make test` passes (all tests, including BDD).
   - `coderabbit review --agent` finds no outstanding concerns.

6. **Roadmap is updated**:
   - `docs/podbot-roadmap.md` (if it exists) marks task 4.2.2 as "done".

7. **PR is created**:
   - PR title includes `(4.2.2)` and describes the feature.
   - PR description includes execplan link and lody session link.

### Red-Green-Refactor evidence

For each major function (`canonicalize_workspace_path`, `enforce_allowlist`, `validate_mount_permissions`):

1. **Red**: Test fails before implementation. Example output:
   ```
   test workspace_mounts::tests::test_canonicalize_simple_path ... FAILED
   
   thread 'workspace_mounts::tests::test_canonicalize_simple_path' panicked at
   'cannot find path canonicalization implementation'
   ```

2. **Green**: Minimal implementation makes test pass. Example output:
   ```
   test workspace_mounts::tests::test_canonicalize_simple_path ... ok
   ```

3. **Refactor**: Improve readability, extract helpers, add more tests. All tests still pass:
   ```
   test result: ok. 25 passed; 0 failed; 0 ignored; 0 measured
   ```

### BDD evidence

1. **Red**: Scenarios fail before integration. Example:
   ```
   Scenario: Mount a workspace within the allowlist ... FAILED
   Given a configured allowed root "/tmp/workspaces" ... undefined step
   ```

2. **Green**: Steps are defined and scenarios pass:
   ```
   Scenario: Mount a workspace within the allowlist ... PASSED
   Scenario: Reject workspace outside the allowlist ... PASSED
   Scenario: Reject symlink escapes ... PASSED
   Scenario: Reject paths with symlinks in any component ... PASSED
   ```


## Idempotence and recovery

All steps are idempotent:

- **Code edits** can be repeated without side effects (overwriting the same content).
- **Test runs** do not modify the filesystem (tests use temporary directories via `tempfile` crate).
- **Configuration validation** runs at startup and reports errors clearly (no partial state).

If a step fails:

1. Identify the failure (check error message or test output).
2. Fix the root cause (update code or tests).
3. Re-run the step or the entire test suite.
4. Commit the fix as a new commit (not an amendment).

No rollback is needed; all changes are forward-only.


## Interfaces and dependencies

### New module: `src/workspace_mounts.rs`

Public interface:

```rust
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq)]
pub enum WorkspaceMountError {
    SymlinkDetected(PathBuf),
    SymlinkCycle(PathBuf),
    DepthExceeded,
    NotInAllowlist(PathBuf),
    NoAllowedRoots,
    NotFound,
    PermissionDenied,
    NotADirectory,
    NotWritable,
}

pub fn canonicalize_workspace_path(path: &Path) -> Result<PathBuf, WorkspaceMountError>;

pub fn enforce_allowlist(
    canonical_path: &Path,
    allowed_roots: &[PathBuf],
) -> Result<(), WorkspaceMountError>;

pub fn validate_mount_permissions(
    path: &Path,
    container_uid: u32,
    container_gid: u32,
) -> Result<(), WorkspaceMountError>;

pub fn mount_workspace(
    path: &Path,
    allowed_roots: &[PathBuf],
    container_uid: u32,
    container_gid: u32,
) -> Result<PathBuf, WorkspaceMountError>;
```

### Extension to `src/config.rs`

Add to the configuration struct:

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkspaceMountConfig {
    pub allowed_roots: Vec<PathBuf>,
}

// In the main Config struct:
pub workspace_mounts: WorkspaceMountConfig,
```

### Dependencies

All dependencies are from Rust standard library. No external crates are added unless a tolerance exception is escalated and approved.

### Testing infrastructure

- **Unit tests**: Use Rust's built-in `#[cfg(test)]` and `#[test]` macros.
- **Fixtures**: Use `tempfile` crate for temporary directories (already a dev dependency).
- **BDD**: Use `cucumber` crate (if available; otherwise manual step definitions).
- **Snapshots**: Use `insta` crate for snapshot testing of error messages (if available; otherwise golden files).
- **Property tests**: Use `proptest` crate for property-based testing (if available; otherwise skip this phase).


## Artifacts and notes

### Key design decisions

1. **Canonicalization always requires resolution**: A path is rejected if it contains any symlinks after canonicalization. This is stricter than filesystem tools but more secure (prevents TOCTOU).

2. **Allowlist is configuration-based**: Operators must explicitly configure allowed roots; Podbot never infers them. This enforces explicit security boundaries.

3. **Permission validation is strict**: A directory must be writable by the container to be mounted. Attempts to mount read-only directories fail with a clear error.

4. **Error messages are actionable**: Each error variant includes enough information for an operator to understand why a mount failed and how to fix it.

### Example configuration

```yaml
podbot:
  workspace_mounts:
    allowed_roots:
      - /tmp/workspaces
      - /home/ci/work
```

### Example usage

```bash
podbot launch \
  --workspace /tmp/workspaces/my-job \
  --container ubuntu:latest
```

Outcome:
- If `/tmp/workspaces/my-job` exists, is within `/tmp/workspaces`, and is writable, the mount succeeds.
- If `/tmp/workspaces/my-job` contains a symlink or is outside the allowlist, the mount fails with a clear error.

### Testing examples

```rust
#[test]
fn test_canonicalize_simple_path() {
    let result = canonicalize_workspace_path(Path::new("/tmp/work"));
    assert!(result.is_ok());
    assert_eq!(result.unwrap().to_string_lossy(), "/tmp/work");
}

#[test]
fn test_enforce_allowlist_within_root() {
    let path = PathBuf::from("/tmp/workspaces/job123");
    let roots = vec![PathBuf::from("/tmp/workspaces")];
    let result = enforce_allowlist(&path, &roots);
    assert!(result.is_ok());
}

#[test]
fn test_enforce_allowlist_outside_root() {
    let path = PathBuf::from("/var/work");
    let roots = vec![PathBuf::from("/tmp/workspaces")];
    let result = enforce_allowlist(&path, &roots);
    assert!(matches!(result, Err(WorkspaceMountError::NotInAllowlist(_))));
}
```


---

## Revision note

(None yet; to be updated if the plan is revised.)
