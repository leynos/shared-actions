# 15.1.4 — Add formal verification Makefile targets

This ExecPlan (execution plan) is a living document. The sections `Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Status: DRAFT


## Purpose / big picture

This plan adds Makefile targets for bounded model checking (Kani) and deductive verification (Verus), enabling the engineering team to verify protocol invariants and message-handling logic with formal guarantees. After completion, developers and CI can run `make test-verification` for quick proofs, `make kani` and `make verus` for targeted verification, and `make kani-full` and `make formal-nightly` for exhaustive bounded exploration. The infrastructure will be validated through `mbake validate Makefile` and exit cleanly on a fresh tree.

Success is observable: developers can run `make test-verification`, `make kani`, `make verus`, `make kani-full`, `make formal-pr`, and `make formal-nightly` targets and see consistent, meaningful output with exit code 0 on a clean tree.


## Constraints

- All new Makefile targets must be accepted by `mbake validate Makefile` without warnings or errors.
- The targets must not modify existing `make test`, `make all`, or other established targets' behaviour; formal verification gates are **separate** from unit tests.
- Installation and version management must use `rust-prover-tools` as the canonical installer and runner (via `prover-tools kani` and `prover-tools verus` CLI).
- Version pins for Kani and Verus must be stored in `tools/kani/VERSION` and `tools/verus/VERSION` as plain-text semantic versions (e.g., `1.2.3`).
- Verus requires `tools/verus/SHA256SUMS` for checksum verification; this file must exist and match the release archive.
- The new targets must follow the project's existing Makefile naming convention (e.g., `check-X` for validation, `make test-verification` as a composite, `kani-full` for nightly).
- No modification to the `.github/workflows/ci.yml` as part of this task; CI gating is out of scope.
- Rust toolchain and `cargo` must be available; the plan assumes Rust 1.89+ (per rust-toy-app/Cargo.toml).
- The targets must be idempotent: running them multiple times on the same state must produce consistent exit codes and output.


## Tolerances (exception triggers)

- **Scope**: If Makefile modifications exceed 200 lines of net additions (including comments and blank lines), stop and escalate.
- **Dependencies**: If a new external dependency beyond `rust-prover-tools`, `kani-verifier`, and `verus` is required, stop and escalate.
- **Public API changes**: If implementation requires changes to public Rust API or CI pipeline integration, stop and escalate.
- **Complexity**: If a single target requires more than three sub-commands or complex conditional logic, reconsider simplification and escalate if unclear.
- **Validation failures**: If `mbake validate Makefile` fails after target addition, or if targets do not exit 0 on a clean tree, stop, document the error, and escalate.


## Risks

- **Risk**: Kani and Verus are computationally intensive; without resource limits, CI could time out or consume excessive system resources.
  - Severity: medium
  - Likelihood: medium
  - Mitigation: Implement fast local tiers (`make kani`, `make verus`) with reasonable bounds, separate from full runs (`make kani-full`, `make formal-nightly`). Default local runs use `--jobs` constraints; nightly runs accept expanded resource allocation.

- **Risk**: Version pinning or toolchain mismatches cause installer failures (e.g., missing `tools/verus/SHA256SUMS` or incompatible Rust toolchain).
  - Severity: high
  - Likelihood: low
  - Mitigation: Validate that version files exist and contain valid semantic versions before writing targets. Document the expected structure in the Makefile. Add explicit `prover-tools <tool> check-version` calls to detect issues early.

- **Risk**: Proof harnesses or specs do not yet exist in the codebase (current state: NONE), so Makefile targets will have no proofs to run initially.
  - Severity: medium (affects acceptance testing)
  - Likelihood: high (confirmed by baseline assessment)
  - Mitigation: Create minimal placeholder proof harnesses for both Kani and Verus to validate target infrastructure. Use `#[cfg(kani)]` gates for harnesses (Kani standard practice). Document the harness locations in the Makefile or a comment block so future work knows where to add proofs.

- **Risk**: Makefile validation tool (`mbake`) has specific syntax or formatting requirements that conflict with emerging best practices.
  - Severity: low
  - Likelihood: low
  - Mitigation: Validate early and often. Run `mbake validate Makefile` after each target addition.

- **Risk**: Parallel test execution (pytest with xdist) and sequential formal verification (Kani/Verus, CPU-intensive) may cause resource contention.
  - Severity: medium
  - Likelihood: low (mitigated by keeping gates separate)
  - Mitigation: Formal verification targets remain separate from `make test`; CI can choose to skip formal verification on fast paths and gate only on nightly/PR workflows.


## Progress

Use a list with checkboxes to summarise granular steps. Every stopping point must be documented here.

- [ ] Stage A: Research and propose architecture (complete survey of Makefile patterns, tooling, risk)
- [ ] Stage B: Design execplan and validate with expert panel
- [ ] Stage C: Create version pin files and placeholder proofs (minimal Red-Green-Refactor)
- [ ] Stage D: Implement Makefile targets and validate with `mbake validate`
- [ ] Stage E: Test targets on clean tree (exit code 0)
- [ ] Stage F: Documentation updates and final validation


## Surprises & discoveries

- **Finding**: The project currently has **zero formal verification infrastructure**. Kani is mentioned in one DRAFT execplan (3-14-5) but no integration exists.
  - Evidence: No `.kani` directories, no Verus specs, no `tools/kani` or `tools/verus` directories, no CI steps for verification.
  - Impact: This task is greenfield; targets must be designed from scratch with no existing harnesses to validate against. Placeholder proofs are essential for target validation.

- **Finding**: Project uses Makefile patterns with variable abstraction and environment overrides (e.g., `UV ?= ...`, `RUFF_FIX_RULES ?= ...`).
  - Evidence: Observed in existing `test`, `fmt`, `lint` targets.
  - Impact: New targets should follow the same pattern for `JOBS`, `KANI_SOLVER`, `VERUS_TIMEOUT`, etc., allowing developers to tune resource allocation.

- **Finding**: The Rust codebase lives in `rust-toy-app/` as a sub-workspace with mature test infrastructure (rstest, cucumber, property tests).
  - Evidence: `/tmp/lody-title-agent/rust-toy-app/Cargo.toml` includes `rstest`, `hypothesis`, `proptest`, `insta`, `cucumber`, `assert_cmd`.
  - Impact: Proof harnesses should live in `rust-toy-app/` under a `#[cfg(kani)]` gated module or in a dedicated `verus/` directory at the project root (per verus conventions). Coordination with rust-toy-app test structure is needed.


## Decision log

- **Decision**: Use `rust-prover-tools` as the canonical installer and runner.
  - Rationale: It is explicitly documented as the canonical installer in the verus and kani skills. It handles version pinning, checksum verification, and toolchain management. It provides a stable CLI interface for both tools.
  - Date/Author: Planning phase (2026-06-22).

- **Decision**: Keep formal verification gates **separate** from `make test` and `make all`.
  - Rationale: Kani and Verus are computationally expensive and serve a different purpose than unit tests. Separating them allows developers to run fast unit tests locally (make test) and formal verification on demand or in nightly CI. This aligns with the project's existing "measure twice, cut once" philosophy of intentional gating.
  - Date/Author: Planning phase (2026-06-22).

- **Decision**: Define two tiers: fast local (`make kani`, `make verus`) and comprehensive (`make kani-full`, `make formal-nightly`).
  - Rationale: Fast local runs enable developer feedback within minutes; comprehensive nightly runs can exhaust the solver with higher resource allocation. This is standard practice in Rust verification projects using Kani/Verus.
  - Date/Author: Planning phase (2026-06-22).

- **Decision**: Create placeholder proof harnesses to validate Makefile target infrastructure before substantive proofs exist.
  - Rationale: The project currently has no Kani or Verus proofs. Without placeholders, targets cannot be validated as working. Placeholders are minimal, gated, and easy to extend or replace as proof work proceeds.
  - Date/Author: Planning phase (2026-06-22).


## Outcomes & retrospective

(To be completed upon final implementation.)


## Context and orientation

The lody-title-agent project is a Rust/Python hybrid codebase with a Rust sub-workspace at `rust-toy-app/`. The project uses:

- **Makefile** (`/tmp/lody-title-agent/Makefile`) for quality gates: `fmt`, `lint`, `typecheck`, `test`, `markdownlint`, `nixie`.
- **Cargo.toml** at `rust-toy-app/Cargo.toml` with mature test frameworks (rstest, proptest, pytest, hypothesis).
- **ExecPlans** as living documents in `docs/execplans/` to guide significant feature work.
- **CI/CD** via `.github/workflows/` with Python pytest and Rust build matrix.

Current state:

- **No formal verification infrastructure**: No Kani harnesses, Verus specs, version pins, or CI steps exist.
- **Mature testing**: Unit tests (rstest, pytest), property tests (hypothesis, proptest), snapshot tests (insta, syrupy), BDD (cucumber, pytest-bdd).
- **Makefile conventions**: Targets are named `check-X` or `test-X` for validation, use environment variable abstraction (e.g., `TOOL ?= ...`), and chain sequentially with explicit dependencies.

The task is to add Makefile targets for bounded model checking (Kani) and deductive verification (Verus) such that developers and CI can invoke formal verification on demand. The targets are infrastructure; substantive proof harnesses will be added in later work (e.g., task 15.2 or 15.3).


## Plan of work

### Stage A: Research and validation (complete)

Survey existing formal verification tooling (Kani, Verus, rust-prover-tools), project Makefile patterns, and baseline state. Identify risks, validate feasibility, and prepare for design review.

**Evidence**: Agent research completed; findings documented above.

### Stage B: Design and expert review (in progress)

Synthesize research into an executable design. Present the execplan to the user for approval before implementation.

### Stage C: Version pin files and placeholder proofs

Create the version pin files that `rust-prover-tools` expects:

- `tools/kani/VERSION`: Plain-text semantic version (e.g., `1.2.3`) for the pinned Kani release.
- `tools/verus/VERSION`: Plain-text semantic version for the pinned Verus release.
- `tools/verus/SHA256SUMS`: SHA256 checksum of the Verus release archive (obtained from GitHub releases).

Create minimal placeholder Kani and Verus proofs:

- **Kani harness** at `rust-toy-app/src/kani.rs` (or module inside existing source), gated with `#[cfg(kani)]`:
  ```rust
  #[cfg(kani)]
  mod kani_proofs {
      #[kani::proof]
      fn verify_placeholder() {
          // Placeholder harness: verify a simple invariant.
          let x = kani::any::<u32>();
          kani::assume(x < 100);
          kani::assert(x < 200, "x is less than 200");
      }
  }
  ```

- **Verus spec** at `verus/placeholder.rs` (project root), minimal proof:
  ```rust
  use vstd::prelude::*;

  verus! {
  
  proof fn lemma_basic_arithmetic() {
      let x: int = 5;
      assert(x + 1 == 6) by { /* auto */ };
  }
  
  }
  ```

Run Red-Green-Refactor to validate:
- Red: Target `make kani` fails because no Kani binary is installed (expected).
- Green: `prover-tools kani install` and re-run `make kani`; harness passes.
- Refactor: Simplify harness or test helper as needed.

**Validation**: Both placeholders compile and pass verification.

### Stage D: Implement Makefile targets

Add six targets to `/tmp/lody-title-agent/Makefile`:

1. **`test-verification`** (composite): Runs `kani` and `verus` sequentially.
   ```makefile
   .PHONY: test-verification
   test-verification: kani verus  ## Run all formal verification (fast local)
   ```

2. **`kani`** (primary): Installs pinned Kani and runs proofs.
   ```makefile
   .PHONY: kani
   kani: install-kani  ## Run Kani bounded model checking (fast, local)
       prover-tools kani check-version
       cargo kani --quiet
   ```

3. **`kani-full`** (nightly): Exhaustive Kani run with higher resource allocation.
   ```makefile
   .PHONY: kani-full
   kani-full: install-kani  ## Run Kani verification suite (comprehensive, nightly)
       prover-tools kani check-version
       cargo kani --quiet --coverage
   ```

4. **`verus`** (primary): Installs pinned Verus and runs proofs.
   ```makefile
   .PHONY: verus
   verus: install-verus  ## Run Verus deductive verification (fast, local)
       prover-tools verus check-version
       prover-tools verus run --proof-file verus/placeholder.rs
   ```

5. **`formal-pr`** (CI/PR gate): Runs `test-verification` and reports results.
   ```makefile
   .PHONY: formal-pr
   formal-pr: test-verification  ## Formal verification gate for PR workflows
   ```

6. **`formal-nightly`** (nightly CI): Runs exhaustive verification tiers.
   ```makefile
   .PHONY: formal-nightly
   formal-nightly: install-kani install-verus  ## Formal verification nightly (comprehensive)
       kani-full
       verus
   ```

**Installation helpers** (internal):
```makefile
.PHONY: install-kani install-verus
install-kani:
	prover-tools kani install

install-verus:
	prover-tools verus install
```

All targets follow project conventions:
- Phony targets declared with `.PHONY`.
- Two-space indentation.
- Descriptive help strings (## comment).
- Use of `prover-tools` CLI for version management and execution.
- Explicit dependency chain: install → check-version → run.

**Validation**: `mbake validate Makefile` returns exit 0.

### Stage E: Test on clean tree

1. Run `make test-verification` on a clean repository state (no proofs committed, minimal harness).
   - Expected: Both Kani and Verus complete successfully, exit 0.
   - Observe: Clear output showing harness status and timing.

2. Run `make kani-full` and `make formal-nightly`.
   - Expected: Exhaustive verification completes (may take minutes), exit 0.

3. Validate idempotence: run the same target twice in a row.
   - Expected: Second run skips reinstalls and runs proofs again with consistent output, exit 0.

4. Confirm `make all` still works and does not invoke formal verification.
   - Expected: `make all` runs fmt, lint, typecheck, test; exits 0. No Kani/Verus output.

### Stage F: Documentation updates

1. Add a comment block to the Makefile describing the formal verification targets and their intended use:
   ```makefile
   # Formal verification targets (Kani bounded model checking, Verus deductive proofs).
   # These targets are separate from unit tests and are intended for development and nightly CI.
   # Version pins: tools/kani/VERSION, tools/verus/VERSION, tools/verus/SHA256SUMS.
   # See docs/formal-verification-methods-in-wireframe.md for detailed guidance.
   ```

2. Update `docs/developers-guide.md` to document:
   - How to run `make test-verification`, `make kani`, `make verus` locally.
   - Where to add new Kani harnesses (`rust-toy-app/`, gated with `#[cfg(kani)]`).
   - Where to add new Verus specs (`verus/` directory).
   - Links to Kani and Verus documentation.

3. Update `AGENTS.md` or a new `docs/formal-verification-guide.md` with:
   - Guidance on when to use Kani vs. Verus.
   - Harness and proof structure conventions.
   - Reference links to skills (`kani`, `verus`).

4. Mark roadmap item 15.1.4 as "done" (once a roadmap.md file is created).

**Validation**: All documentation is clear, up-to-date, and links are functional.


## Validation and acceptance

### Red-Green-Refactor evidence

**Red phase**: Add empty or placeholder Makefile targets; run `make kani` → fails with "Kani not installed" (expected).

**Green phase**: Install `prover-tools kani` and minimal harness; run `make kani` → passes with exit 0.

**Refactor phase**: Verify targets are idempotent and produce consistent output on repeated runs.

### Quality criteria

- **Makefile validation**: `mbake validate Makefile` returns exit 0.
- **Target execution**: All six targets (`test-verification`, `kani`, `verus`, `kani-full`, `formal-pr`, `formal-nightly`) return exit 0 on a clean tree with placeholder proofs.
- **Idempotence**: Running a target twice produces consistent output and exit code.
- **Separation of concerns**: `make test` and `make all` are unaffected; formal verification is opt-in via new targets.
- **Documentation**: All targets have help strings (## comment); developers-guide.md updated with guidance.
- **Proof presence**: Placeholder Kani and Verus proofs exist and pass; location documented for future work.

### Quality method

1. **Makefile syntax**: Run `mbake validate Makefile` after each target addition.
2. **Target execution**: Run each target manually on a clean tree and confirm exit 0.
3. **Integration**: Run `make all` and confirm no regressions; run `make test-verification` and confirm all gates pass.
4. **Documentation**: Review help strings and developers-guide updates for clarity.
5. **Code review**: Use `coderabbit review --agent` after implementation to catch any issues.

### Expected outputs

**`make test-verification` on a clean tree:**
```
prover-tools kani check-version
Kani Rust Verifier version 1.2.3
Running Kani verification...
Harness 'verify_placeholder': SUCCESSFUL
Proof status: 1 harness verified.
Exit code: 0

prover-tools verus check-version
Verus version 0.3.0
Running Verus verification...
Verification complete: 0 errors.
Exit code: 0
```

**`mbake validate Makefile`:**
```
Validating Makefile...
✓ All syntax checks passed.
Exit code: 0
```


## Idempotence and recovery

All targets are idempotent:

- **Installation targets** (`install-kani`, `install-verus`) check whether the tool is already installed and skip reinstallation if version matches.
- **Verification targets** run proofs without modifying source files.
- **Composite targets** chain dependencies; re-running skips reinstalls.

**Recovery**: If a target fails, fix the underlying issue (e.g., add missing version pin file or proof harness) and re-run. No cleanup required.


## Artifacts and notes

**Makefile targets (excerpt):**
```makefile
.PHONY: test-verification kani kani-full verus formal-pr formal-nightly
.PHONY: install-kani install-verus

test-verification: kani verus
	@echo "Formal verification complete."

kani: install-kani
	prover-tools kani check-version
	cargo kani --quiet

kani-full: install-kani
	prover-tools kani check-version
	cargo kani --quiet --coverage

verus: install-verus
	prover-tools verus check-version
	prover-tools verus run --proof-file verus/placeholder.rs

formal-pr: test-verification

formal-nightly: install-kani install-verus
	$(MAKE) kani-full
	$(MAKE) verus

install-kani:
	prover-tools kani install

install-verus:
	prover-tools verus install
```

**Version pin files:**
- `tools/kani/VERSION`: (e.g., `1.2.3`)
- `tools/verus/VERSION`: (e.g., `0.3.0`)
- `tools/verus/SHA256SUMS`: (SHA256 checksum from release)

**Placeholder proofs:**
- `rust-toy-app/src/kani.rs`: Minimal Kani harness with `#[cfg(kani)]` gate.
- `verus/placeholder.rs`: Minimal Verus proof.


## Interfaces and dependencies

**Dependencies:**
- `rust-prover-tools` (CLI for Kani and Verus, already expected to be installed or auto-installed by `prover-tools`).
- `kani-verifier` (installed via `prover-tools kani install`).
- `verus` (downloaded and verified via `prover-tools verus install`).
- Rust 1.89+ (per rust-toy-app/Cargo.toml).

**Makefile interfaces:**
- New targets are all phony (`.PHONY: test-verification kani verus ...`).
- New targets return exit code 0 on success, non-zero on failure.
- New targets accept environment variable overrides for resource allocation (e.g., `JOBS`, `VERUS_TIMEOUT`).

**No changes to public API or crate interfaces**: Formal verification is purely additive infrastructure.
