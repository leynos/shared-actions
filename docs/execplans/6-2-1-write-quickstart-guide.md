# Write Quickstart Guide for shared-actions (6.2.1)

This ExecPlan (execution plan) is a living document. The sections `Constraints`,
`Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`,
and `Outcomes & Retrospective` must be kept up to date as work proceeds.

## Status

DRAFT

## Purpose / big picture

The shared-actions repository contains 17 reusable GitHub Actions focused on
Rust and Python build, test, package, and release automation. Today, new users
face a steep discovery curve: the README provides only a bare table listing
actions with no guidance on which ones to use, how they fit together, or how to
compose them into working workflows. Individual action READMEs exist but are
isolated reference docs showing single-action usage patterns, not realistic
end-to-end pipelines.

This ExecPlan will deliver `docs/quickstart.md`, a scenario-driven onboarding
guide that bridges the gap between the action table and deep reference
documentation. After following this guide, a user new to shared-actions will be
able to:

1. Understand what these actions do and which ones solve their problem
   (within 5 minutes of reading)
2. See a complete, runnable workflow example for their use case (Rust
   build+release, Python PyPI publish, coverage measurement, or Dependabot
   auto-merge)
3. Know how to customize the example for their project and where to find
   deeper docs
4. Understand common pitfalls and security considerations when using GitHub
   Actions

The guide will be validated by running all provided YAML examples through the
project's existing action-validator tool and composability smoke tests to
ensure they produce expected outputs and work end-to-end.

## Constraints

Hard invariants that must hold throughout implementation.

- **Scope:** The guide must remain a quickstart (max 1,200 lines including
  YAML examples). Complex topics (Cargo configuration, nFPM templating,
  platform-specific cross-compilation) link to existing deep-dive
  documentation rather than being explained in full.

- **Content source:** Every action mentioned in the guide already exists in
  this repository. No new actions are created as part of this task. All
  examples derive from or are validated against existing action tests and
  documented workflows (`.github/workflows/ci.yml`, test fixtures).

- **Linking over duplication:** When explaining concepts (composite actions,
  reusable workflows, caching, GitHub token permissions), link to existing
  reference docs (AGENTS.md, developers-guide.md, individual action READMEs)
  rather than duplicating explanation.

- **Single source of truth:** The master table of actions remains in the root
  README.md. The quickstart guide adds narrative and examples but does not
  replace that table.

- **Action versioning:** Examples use published major-version tags for remote
  usage (e.g., `owner/shared-actions/.github/actions/setup-rust@v1`) and
  local-path syntax for development (`./.github/actions/setup-rust`).
  Examples must match the current semantic versioning strategy documented in
  AGENTS.md.

- **Platform coverage:** Examples must be valid YAML and runnable on at least
  ubuntu-latest. Platform-specific gotchas (Windows GUI tools, macOS
  cross-compilation, Linux systemd services) must be explicitly noted or
  omitted.

- **No breaking changes:** Implementing this guide must not require changes to
  existing actions, workflows, or tooling. If such changes are discovered to
  be necessary, stop and escalate.

## Tolerances (exception triggers)

Thresholds that trigger escalation when breached.

- **Scope creep:** If the guide grows beyond 1,200 lines (excluding code
  examples) or requires adding more than 4 complete YAML scenario examples,
  stop and escalate.

- **Example validation failure:** If any provided YAML example fails
  `action-validator` or would fail on a representative CI runner
  (ubuntu-latest), stop with error logs and escalate.

- **Design inconsistencies:** If writing examples reveals that following the
  guide's recommended action sequence would fail at runtime on a specific OS
  or with specific inputs (even if no documentation contradiction exists),
  file an issue with a minimal reproduction and escalate. Do not work around
  the inconsistency with undocumented workarounds.

- **Documentation conflicts:** If two action READMEs contradict each other
  (e.g., conflicting input defaults, incompatible output formats), file a
  GitHub issue and escalate rather than proceeding.

- **Ambiguous target audience:** If it becomes unclear whether the guide
  should prioritize Rust users, Python users, or equal coverage, escalate.
  The current decision is: Rust-primary, with Python as a secondary example,
  since shared-actions is primarily a Rust tooling library.

- **Missing testing infrastructure:** If the guide requires inventing new test
  fixtures, CI workflows, or smoke-test infrastructure not already present in
  the repository, escalate.

- **Scope definition:** The quickstart will include full, runnable YAML
  examples for exactly these actions: (1) `setup-rust`, (2)
  `rust-build-release`, (3) one platform packager (`linux-packages`
  preferred), (4) `release-to-pypi-uv`. All other actions receive 1-line
  descriptions + links to individual READMEs. Deviations from this list
  trigger scope-creep escalation.

## Risks

Known uncertainties that might affect the plan.

- **Risk:** Examples become stale as actions evolve.
  Severity: high
  Likelihood: high
  Mitigation: Cross-reference each example against corresponding `action.yml`
  files. Add comments pinpointing exact input versions. Include a validation
  date in the guide (e.g., "validated against shared-actions v1.2.3,
  2026-06-18"). Establish a quarterly review cadence to refresh examples
  against the latest action defaults.

- **Risk:** Action interdependencies are more complex than documented.
  Severity: high
  Likelihood: high
  Mitigation: Before writing examples, create a dependency matrix
  distinguishing: (a) always-required (e.g., `setup-rust` before compilation),
  (b) conditionally-required (e.g., `linux-packages` only for release builds),
  (c) optional (e.g., coverage generation). Document this matrix upfront in
  the guide to set correct expectations.

- **Risk:** Examples work on ubuntu-latest but fail on macOS or Windows
  runners.
  Severity: medium
  Likelihood: medium
  Mitigation: Validate Rust examples on ubuntu-latest and macos-latest if
  feasible. For platform-specific actions (e.g., `windows-package`), test
  locally or note the limitation. Clearly annotate platform-specific sections
  in the guide.

- **Risk:** First-time users are confused by the difference between actions
  (reusable steps) and reusable workflows (full workflow files).
  Severity: medium
  Likelihood: medium
  Mitigation: Include a brief comparison section early in the guide. Link to
  existing doc: docs/composite-actions-vs-full-workflows.md.

- **Risk:** Users skip the quickstart and go directly to individual action
  READMEs, missing the narrative context.
  Severity: low
  Likelihood: medium
  Mitigation: Add a prominent "Start here" link in the root README.md pointing
  to the new quickstart.

- **Risk:** Glossary terms are unfamiliar to GitHub Actions newcomers.
  Severity: low
  Likelihood: medium
  Mitigation: Include a "Glossary" section at the end of the guide. Define:
  composite action, reusable workflow, runner, sccache, nextest, nFPM,
  artefacts, staging.

## Progress

Use a list with checkboxes to summarise granular steps. Every stopping point
must be documented here.

- [ ] (TBD) Phase 1: Research & scope clarification
  - [ ] Confirm Rust-primary vs equal coverage decision
  - [ ] Create dependency matrix for actions
  - [ ] Finalize file path and location (docs/quickstart.md vs
  docs/getting-started.md)
  - [ ] Define maintenance owner and update process

- [ ] (TBD) Phase 2: Outline & structure
  - [ ] Draft the guide outline section by section
  - [ ] Select 3–4 representative scenarios (Rust build, Python release,
  coverage, auto-merge)
  - [ ] Create a spreadsheet of action categories and dependencies
  - [ ] Identify which existing docs to link vs which content to inline

- [ ] (TBD) Phase 3: Content creation
  - [ ] Write hero section and prerequisites
  - [ ] Write "What's Inside" section with category overview
  - [ ] Write Scenario A: Rust binary build + release (with full YAML)
  - [ ] Write Scenario B: Python package to PyPI (with full YAML)
  - [ ] Write Scenario C: Coverage measurement (with example)
  - [ ] Write Scenario D: Dependabot auto-merge (with link to workflow)
  - [ ] Write "Understanding Action Dependencies" section
  - [ ] Write "Common Patterns" section
  - [ ] Write "Troubleshooting" section
  - [ ] Write "Next Steps" section
  - [ ] Add "Glossary" section

- [ ] (TBD) Phase 4: Validation & testing
  - [ ] Run all YAML examples through `make lint` (action-validator)
  - [ ] Verify all internal doc links (to AGENTS.md, developers-guide.md, etc.)
  - [ ] Create minimal smoke-test workflows for each scenario
  - [ ] Validate on ubuntu-latest runner
  - [ ] Peer review for clarity and accuracy
  - [ ] Ensure consistent tone and voice

- [ ] (TBD) Phase 5: Integration & merge
  - [ ] Update root README.md to link to quickstart
  - [ ] Add quickstart link to AGENTS.md "Next Steps"
  - [ ] Add maintenance notes to execplan Outcomes section
  - [ ] Create draft PR with comment linking to this execplan
  - [ ] Incorporate review feedback
  - [ ] Merge with all CI gates passing

## Surprises & discoveries

Unexpected findings during implementation that were not anticipated as risks. To
be updated as work proceeds.

(No surprises yet; implementation has not begun.)

## Decision log

Record every significant decision made while working on the plan.

- **Decision:** Prioritize Rust-primary content with Python as secondary
example.
  Rationale: The action catalog contains 16 Rust-specific actions and only 1
  Python-specific action (release-to-pypi-uv). A guide treating both equally
  would create asymmetric expectations. Python users can reference external PyPI
  docs; Rust users have minimal alternatives within shared-actions.
  Date/Author: 2026-06-18 / execplan research phase.

- **Decision:** Limit examples to 4 actions (setup-rust, rust-build-release,
linux-packages, release-to-pypi-uv).
  Rationale: Prevents scope creep. All other actions receive mention + link. A
  user seeking deeper guidance on a specific action reads that action's
  individual README.
  Date/Author: 2026-06-18 / expert review gap #3.

- **Decision:** Create dependency matrix before writing examples.
  Rationale: Addresses expert review gap #1. Actions have conditional
  dependencies (setup-rust is optional if user already has Rust; linux-packages
  is only needed for release builds). A matrix clarifies when each action is
  required vs optional.
  Date/Author: 2026-06-18 / expert review gap #1.

- **Decision:** Assign explicit maintenance owner.
  Rationale: Addresses expert review gap #6. Guide must be kept current as
  actions evolve. Owner is responsible for quarterly review and updating
  examples when action defaults change materially.
  Date/Author: 2026-06-18 / expert review gap #6.

- **Decision:** Use `docs/quickstart.md` as the filename.
  Rationale: Consistent with GitHub's documentation convention. Discoverable
  from docs/ directory and linkable from README.md.
  Date/Author: 2026-06-18 / expert review gap #6.

(Additional decisions to be recorded as implementation proceeds.)

## Outcomes & retrospective

To be updated at major milestones or completion. Compare result against purpose
and note lessons learned.

(Outcomes to be recorded upon completion.)

## Context and orientation

The shared-actions repository is a GitHub-hosted collection of 17 reusable
GitHub Actions and 1 reusable workflow, focused on automating build, test,
package, and release workflows for Rust and Python projects.

**Key files:**

- **README.md** (root): Master table of 17 actions with major version and
path. This is the entry point users see.
- **docs/developers-guide.md**: Internal architecture for contributors. Covers
concurrency assumptions, venv management, caching strategies.
- **docs/composite-actions-vs-full-workflows.md**: Explains when to use actions
vs workflows.
- **docs/generate-coverage-design.md**: Deep dive into coverage tooling
(slipcover, pytest-xdist).
- **docs/rust-build-release-pipeline.md**: Detailed guide to Rust
build/package/release sequencing.
- **AGENTS.md**: Foundational constraints, tool resolution, CI/CD strategies.
- **.github/actions/**: Directory containing 17 action subdirectories, each with
action.yml and README.md.
- **.github/workflows/**: CI workflows (ci.yml, dependabot-automerge.yml) that
exemplify action usage.
- **Makefile**: Contains targets like `fmt`, `lint`, `test`. The `lint` target
runs action-validator.

**Key actions for this guide:**

1. **setup-rust**: Installs Rust toolchain, cargo-binstall, optional DB dev
libraries, cross-compilers.
2. **rust-build-release**: Builds Rust binaries with configurable features and
targets. Outputs staging paths for packagers.
3. **linux-packages**: Creates .deb and .rpm packages using nFPM from staged
binaries.
4. **release-to-pypi-uv**: Publishes Python packages to PyPI using uv.
5. **generate-coverage**: Measures code coverage with slipcover (Python) or
cargo-tarpaulin (Rust).
6. **dependabot-automerge**: Reusable workflow for automatically merging
Dependabot PRs.

**Current documentation gaps:**

- No narrative guide showing how to compose actions into a realistic end-to-end
workflow.
- No clear decision tree for users ("Am I building a Rust binary? A Python
package? Both?").
- Examples exist in ci.yml but are not surfaced or explained to new users.
- Individual action READMEs are reference docs, not tutorials.

**Target audience:**

- First-time users of GitHub Actions or shared-actions specifically (estimated
background: basic GitHub familiarity).
- Maintainers of mono-repositories needing templated CI/CD.
- DevOps engineers building composite workflows.
- Project owners evaluating whether shared-actions fits their pipeline.

**Success criteria (observable):**

1. A new user can read the quickstart in ≤10 minutes and understand: what these
actions do, which ones solve their problem, how to use a chosen action in their
workflow.
2. At least one complete Rust example (setup-rust → rust-build-release →
linux-packages → release asset upload) is provided and validated.
3. At least one Python example (release-to-pypi-uv) is provided and validated.
4. All YAML examples pass `make lint` (action-validator).
5. All internal doc links are correct.
6. A peer reviewer unfamiliar with shared-actions can follow the guide without
external context.

## Plan of work

The work consists of five sequential phases. Each phase has defined go/no-go
validation before proceeding to the next.

### Phase 1: Research & Scope Clarification (2 hours)

**Goal:** Resolve the 6 gaps identified in expert review before writing begins.

#### Steps

1. Create a dependency matrix distinguishing:
   - Always-required actions (e.g., setup-rust before rust-build-release)
   - Conditionally-required actions (e.g., linux-packages only for release
   builds with packaging)
   - Optional actions (e.g., generate-coverage can run independently)

   Document this matrix in a plaintext file (e.g.,
   `docs/execplans/6-2-1-dependency-matrix.txt`) so it's visible during
   implementation.

2. Finalize the 4-action scope: setup-rust, rust-build-release, linux-packages,
release-to-pypi-uv. Confirm this list aligns with the author's intent. If not,
escalate.

3. Decide on file location and hierarchy. File will be `docs/quickstart.md`.
Confirm this path and update this execplan if different.

4. Identify maintenance owner (a person or team responsible for quarterly review
and updates). Document in execplan Outcomes section once identified.

5. Review the four scenarios and their representative use cases:
   - Scenario A (Rust build + release): for maintainers releasing Rust binaries
   - Scenario B (Python PyPI): for maintainers publishing Python packages
   - Scenario C (Coverage): for any project measuring code coverage
   - Scenario D (Dependabot auto-merge): for any project using Dependabot

#### Go/no-go gate

- All 5 items above are completed and documented.
- Author confirms the scope and 4-action list.
- If any item is ambiguous, escalate for clarification before proceeding to
Phase 2.

**Artifacts:** dependency-matrix.txt, updated execplan Decision Log.

---

### Phase 2: Outline & Structure (2 hours)

**Goal:** Draft the guide structure without writing full prose, ensuring
coverage and flow.

#### Steps

1. Create a detailed outline of all sections:
   - Hero section (1-liner, audience, key features)
   - Prerequisites (GitHub Actions knowledge, repo setup)
   - What's Inside (category overview, action selector)
   - Scenario A outline (Rust build + release)
   - Scenario B outline (Python PyPI)
   - Scenario C outline (Coverage)
   - Scenario D outline (Dependabot auto-merge)
   - Understanding Action Dependencies (matrix summary)
   - Common Patterns (matrix builds, conditional steps, caching)
   - Troubleshooting (3–5 common issues)
   - Next Steps (links to deeper docs)
   - Glossary (5–8 key terms)

2. For each scenario, identify:
   - The exact actions to show (which of the 4 core actions apply)
   - The YAML file path and snippet to provide
   - Links to existing docs that provide deeper explanation
   - Expected output or success criteria

3. Create a spreadsheet mapping:
   - Use case → Actions involved → Individual README links → Where in guide

4. Identify all internal doc links (AGENTS.md, developers-guide.md, individual
action READMEs, etc.) and verify they exist.

5. Draft section headers and sub-headers in a skeleton .md file.

#### Go/no-go gate

- Outline is complete with all sections clearly named.
- All internal doc links are verified to exist.
- The 4-action scope is reflected consistently in the outline.
- Author reviews outline and confirms structure.

**Artifacts:** Skeleton `docs/quickstart.md` with headers only; spreadsheet of
action mappings.

---

### Phase 3: Content Creation (4–5 hours)

**Goal:** Write all narrative and code sections, validating YAML syntax as you
go.

#### Steps

1. **Hero & Prerequisites section** (30 min):
   - Write 1-line description: "Reusable GitHub Actions for Rust and Python
   projects."
   - Explain who this guide is for.
   - List 3–4 key features (build, test, package, release).
   - Explain prerequisites (GitHub Actions familiarity, .github/workflows/
   directory structure).

2. **What's Inside section** (30 min):
   - Provide a category-based overview (Build, Test, Package, Release,
   Utilities).
   - Create a small table mapping categories to representative actions.
   - Add a decision tree: "Are you building Rust, Python, or both? → Go to
   Scenario A/B/C/D."

3. **Scenario A: Rust Binary Build + Release** (60 min):
   - Write prose explaining the use case.
   - Provide a minimal workflow.yml YAML snippet (checkout → setup-rust →
   rust-build-release → linux-packages → upload-release-assets).
   - Annotate the YAML with comments explaining each step.
   - Show typical input values and how to customize.
   - Explain the output (staging directory, release assets).
   - Link to individual action READMEs for deeper customization.
   - **Validation**: Run this YAML through `make lint` (action-validator) and
   verify it passes.

4. **Scenario B: Python Package to PyPI** (30 min):
   - Write prose explaining the use case.
   - Provide a minimal workflow.yml snippet (checkout → release-to-pypi-uv).
   - Annotate with comments.
   - Explain how to set up PyPI credentials/token.
   - Link to action README and external PyPI docs.
   - **Validation**: Run YAML through `make lint` and verify it passes.

5. **Scenario C: Coverage Measurement** (30 min):
   - Explain the use case (measure code coverage across Rust and Python).
   - Provide a workflow snippet showing generate-coverage with lang=rust and
   lang=python in separate jobs.
   - Link to docs/generate-coverage-design.md for deep dive.
   - **Validation**: Run YAML through `make lint`.

6. **Scenario D: Dependabot Auto-Merge** (20 min):
   - Explain the reusable workflow pattern.
   - Show how to reference the workflow in a user's repository.
   - Link to docs/composite-actions-vs-full-workflows.md and the
   dependabot-automerge.yml workflow file.
   - No new YAML needed (workflow already exists in repo).

7. **Understanding Action Dependencies section** (20 min):
   - Present the dependency matrix in prose form (not a table; use bullet
   points).
   - Example: "setup-rust must run before rust-build-release, but is optional if
   your runner already has Rust installed."
   - Explain sequencing rules and when actions can run in parallel.
   - Reference the matrix created in Phase 1.

8. **Common Patterns section** (20 min):
   - Show: local vs remote usage (`./.github/actions/setup-rust` vs
   `owner/shared-actions/.github/actions/setup-rust@v1`).
   - Show: matrix builds across platforms.
   - Show: conditional step execution.
   - Link to developers-guide.md for caching details.
   - **Note:** Keep these as brief examples; deeper guidance lives in linked
   docs.

9. **Troubleshooting section** (20 min):
   - Provide 3–5 common issues and resolutions:
     1. "My build failed in the action but worked locally" → Explain runner
     differences, link to act documentation.
     2. "How do I debug an action step?" → Explain GitHub Actions debug logs and
     ACTIONS_STEP_DEBUG.
     3. "Can I use just one of these actions without the others?" → Yes, they're
     composable; show cherry-pick example.
     4. "My Dependabot PR didn't auto-merge" → Check permissions, token, branch
     protection rules.
     5. "I need to package for Windows, but windows-package is v0" → Explain
     versioning strategy, point to action README.
   - Link to local-validation-of-github-actions-with-act-and-pytest.md.

10. **Next Steps section** (15 min):
    - Link to full AGENTS.md for comprehensive action list.
    - Link to developers-guide.md for architecture deep-dives.
    - Link to individual action READMEs.
    - Link to GitHub Actions official documentation.
    - Contributing guidelines.

11. **Glossary section** (15 min):
    - Define 5–8 terms:
      - Composite action
      - Reusable workflow
      - Runner
      - sccache
      - nextest
      - nFPM
      - Artefacts (staging)
      - Slipcover

#### Go/no-go gate

- All prose sections are written.
- All YAML examples pass `make lint` (action-validator).
- All internal doc links are verified.
- No section is missing.

**Artifacts:** Complete `docs/quickstart.md` file.

---

### Phase 4: Validation & Testing (2–3 hours)

**Goal:** Ensure all examples are correct, testable, and discoverable.

#### Steps

1. **YAML Validation** (30 min):
   - Run `make lint` from repository root.
   - Verify all YAML snippets in the guide pass action-validator.
   - Fix any indentation, syntax, or reference errors.

2. **Link Verification** (30 min):
   - Verify all internal links (to AGENTS.md, developers-guide.md, individual
   action READMEs, etc.) exist and are correct.
   - Use grep or a link checker to catch broken paths.
   - Example: `grep -n "AGENTS.md" docs/quickstart.md` should return valid file
   references.

3. **Composability Smoke Tests** (60 min):
   - For Scenario A (Rust build + release): Create a minimal test workflow that
   runs setup-rust → rust-build-release with a fixture Rust project.
   - For Scenario B (Python PyPI): Create a test workflow that runs
   release-to-pypi-uv with a mock PyPI endpoint or dry-run flag (if supported).
   - For Scenario C (Coverage): Create a test workflow that runs
   generate-coverage with both lang=rust and lang=python.
   - Run each test workflow locally with `act` or in CI.
   - Verify that actions produce expected outputs and no runtime errors occur.

4. **Platform Coverage** (30 min):
   - Validate Rust examples on ubuntu-latest (minimum).
   - If feasible, also test on macos-latest.
   - Document any platform-specific gotchas in the guide.

5. **Peer Review** (30 min):
   - Have a maintainer unfamiliar with the actions read the guide.
   - Gather feedback on clarity, completeness, and tone.
   - Flag any sections that are confusing or require external context.
   - Incorporate feedback.

#### Go/no-go gate

- All YAML passes `make lint`.
- All links are correct.
- Smoke tests run without errors on ubuntu-latest.
- Peer review is complete with no major clarity issues.

**Artifacts:** Validated `docs/quickstart.md`, smoke-test workflow files, peer
review notes.

---

### Phase 5: Integration & Merge (1 hour)

**Goal:** Finalize the guide, update root README, and merge to main branch.

#### Steps

1. **Update root README.md** (15 min):
   - Add a link in the README.md hero or "Next Steps" section pointing to
   docs/quickstart.md.
   - Example: "New to shared-actions? Start with the [Quickstart
   Guide](docs/quickstart.md)."

2. **Update AGENTS.md** (5 min):
   - Add a link to docs/quickstart.md in the "Getting Started" or
   "Documentation" section.

3. **Document maintenance** (10 min):
   - Update this execplan's Outcomes section with:
     - Name and GitHub handle of maintenance owner.
     - Quarterly review schedule.
     - Process for updating examples when action defaults change.

4. **Create draft PR** (15 min):
   - Commit changes (docs/quickstart.md, updated README.md, updated AGENTS.md).
   - Push to branch `6-2-1-write-quickstart-guide`.
   - Create a draft PR with title: "(6.2.1) Write Quickstart Guide for
   shared-actions".
   - In PR description, link to this execplan and summarize what was delivered.
   - Include a "References" section with the lody session link.

5. **Merge with gates passing** (15 min):
   - Ensure all CI/CD gates pass (lint, typecheck, tests if any).
   - Mark PR as ready for review.
   - Await approval and merge to main.

#### Go/no-go gate

- All phases 1–4 are complete and validated.
- PR is open and all CI gates pass.

**Artifacts:** Merged docs/quickstart.md, updated README.md and AGENTS.md,
merged PR.

## Concrete steps

**Working directory:** `/tmp/lody-title-agent` (the shared-actions repository).

### Phase 1: Research & Scope Clarification

```bash
# Step 1: Create dependency matrix
cat > docs/execplans/6-2-1-dependency-matrix.txt << 'EOF'
ACTION DEPENDENCY MATRIX

Always-required (must run before dependent actions):
- setup-rust → rust-build-release (setup-rust installs Rust;
  rust-build-release requires it)

Conditionally-required (needed only for specific workflows):
- rust-build-release → linux-packages (only if you want to create .deb/.rpm packages)
- rust-build-release → macos-package (only if you want to create macOS .pkg)
- rust-build-release → windows-package (only if you want to create Windows .msi/.zip)
- Any build action → generate-coverage (only if measuring coverage)
- generate-coverage → ... (optional; produces artifacts but doesn't
  affect other actions)

Optional (can run independently):
- setup-rust (if runner already has Rust, can skip)
- release-to-pypi-uv (independent Python workflow; does not require any Rust actions)
- dependabot-automerge (separate reusable workflow; does not depend on build actions)

Platform-specific:
- linux-packages requires ubuntu-latest runner
- macos-package requires macos-latest runner
- windows-package requires windows-latest runner
EOF

# Verify it was created
ls -l docs/execplans/6-2-1-dependency-matrix.txt
echo "Matrix created."
```

Expected output:

```text
-rw-r--r--. 1 leynos leynos  822 Jun 18 xx:xx docs/execplans/6-2-1-dependency-matrix.txt
Matrix created.
```

**Go/no-go:** Proceed to Phase 2 if all 5 items from Phase 1 steps are completed
and documented in this execplan's Decision Log.

---

### Phase 2: Outline & Structure

```bash
# Create skeleton docs/quickstart.md with headers only
cat > docs/quickstart.md << 'EOF'
# Quickstart Guide

## Hero Section

## Prerequisites

## What's Inside

### Action Categories

### How to Choose Your Path

## Scenarios

### Scenario A: Build and Release a Rust Binary

### Scenario B: Publish a Python Package to PyPI

### Scenario C: Measure Code Coverage

### Scenario D: Automate Dependabot Merges

## Understanding Action Dependencies

## Common Patterns

## Troubleshooting

## Next Steps

## Glossary
EOF

echo "Skeleton created at docs/quickstart.md"

# Verify links to existing docs
ls -1 docs/{developers-guide,composite-actions,generate-coverage}*.md | head -5
```

Expected output:

```text
Skeleton created at docs/quickstart.md
docs/composite-actions-vs-full-workflows.md
docs/developers-guide.md
docs/generate-coverage-design.md
```

**Go/no-go:** Skeleton exists with all section headers. Outline is complete.
Author confirms structure before proceeding.

---

### Phase 3: Content Creation

This phase involves writing prose for each section. Example validation during
Scenario A:

```bash
# After writing Scenario A YAML, extract and validate it
grep -A 50 "Scenario A:" docs/quickstart.md | grep -A 20 '```yaml' > /tmp/scenario-a.yml

# Run through action-validator
make lint 2>&1 | tee /tmp/lint-output.txt | head -20
```

Expected output if no errors:

```text
[OK] All actions validated successfully.
```

If errors occur, fix YAML indentation and rerun until passing.

---

### Phase 4: Validation & Testing

```bash
# Verify all internal links exist
for link in AGENTS.md developers-guide.md \
    composite-actions-vs-full-workflows.md generate-coverage-design.md; do
  if grep -q "$link" docs/quickstart.md; then
    if [ -f "docs/$link" ] || [ -f "$link" ]; then
      echo "✓ $link exists"
    else
      echo "✗ $link NOT FOUND"
    fi
  fi
done
```

Expected output:

```text
✓ AGENTS.md exists
✓ developers-guide.md exists
✓ composite-actions-vs-full-workflows.md exists
✓ generate-coverage-design.md exists
```

---

### Phase 5: Integration & Merge

```bash
# Add link to root README.md (in "Next Steps" or "Getting Started" section)
# This is a manual edit; update the appropriate section with:
# "New to shared-actions? Start with the [Quickstart Guide](docs/quickstart.md)."

# Commit changes
git add docs/quickstart.md docs/README.md AGENTS.md
git commit -m "Add quickstart guide for shared-actions (6.2.1)

Provides scenario-driven onboarding for new users, including:
- Rust binary build and release example
- Python PyPI publishing example
- Coverage measurement example
- Dependabot auto-merge walkthrough

All examples are validated against action.yml files and pass action-validator."

# Push and create PR
git push -u origin 6-2-1-write-quickstart-guide
```

Expected: Commit message, successful push, PR created.

---

## Validation and acceptance

### Observable behaviour

After following the guide, a new user should be able to:

1. **Understand the purpose:** Run through "What's Inside" section and correctly
identify that setup-rust + rust-build-release is the path for Rust binary
projects.

2. **Run a complete example:** Copy the Scenario A YAML into their repository at
`.github/workflows/release.yml`, adjust project-specific fields (binary name,
target platforms), push to GitHub, and see a successful build and release
workflow execute.

3. **Understand dependencies:** Read the "Understanding Action Dependencies"
section and predict which actions must run in sequence and which are optional.

4. **Find deeper docs:** Locate links to individual action READMEs and
architectural deep-dives for topics they want to customize (e.g., Cargo
features, nFPM configuration).

### Quality criteria

**Content:**

- [ ] Guide is between 600–1,200 lines (excluding code examples).
- [ ] Includes at least 4 distinct use-case scenarios (Rust, Python, coverage,
Dependabot).
- [ ] All YAML examples pass `make lint`.
- [ ] All internal doc links are correct and point to existing files.
- [ ] Glossary covers at least 5 key terms.

**Examples:**

- [ ] Each YAML example is copy-paste runnable (no manual edits beyond
project-specific fields like binary name).
- [ ] Examples are validated against corresponding action.yml files and existing
workflows (ci.yml).
- [ ] At least one Rust example and one Python example are present.
- [ ] Examples include realistic input values and comments explaining how to
customize.

**Validation:**

- [ ] `make lint` passes (no markdown or YAML errors).
- [ ] Peer review by a maintainer unfamiliar with shared-actions is complete
with no unresolved clarity issues.
- [ ] No broken internal or external links.
- [ ] Platform-specific notes (if any) are clearly marked.

**Integration:**

- [ ] Root README.md updated to link to quickstart.
- [ ] AGENTS.md updated to reference quickstart in "Getting Started" or similar
section.
- [ ] New file is discoverable (linked from README.md and AGENTS.md).
- [ ] All CI/CD gates pass with the new file added.

### Commands to verify success

```bash
# Verify file exists and has content
wc -l docs/quickstart.md
# Expected: > 600 lines

# Verify YAML examples pass validation
make lint
# Expected: All checks pass

# Verify links are correct
grep -o '\[.*\](.*\.md)' docs/quickstart.md | head -5
# Expected: Links to AGENTS.md, developers-guide.md, etc.

# Verify GitHub Actions syntax
grep -c '```yaml' docs/quickstart.md
# Expected: >= 3 (at least 3 YAML examples)
```

### Acceptance definition

Delivery is complete when:

1. `docs/quickstart.md` exists with 600–1,200 lines of content.
2. All YAML examples pass `make lint` without errors.
3. Peer review is complete and no major clarity issues remain.
4. Root README.md and AGENTS.md are updated with links to the quickstart.
5. All CI/CD gates pass.
6. A draft PR is open with a reference to this execplan and the lody session.

## Idempotence and recovery

All steps in this plan are idempotent. If a phase fails or is incomplete:

- **Phase 1–2:** Delete and recreate the files (dependency-matrix.txt, skeleton
docs/quickstart.md). No external systems are affected.

- **Phase 3:** Rewrite the prose or YAML sections. Validation in Phase 4 will
catch errors.

- **Phase 4:** Re-run validation steps. Link checks and YAML validation are
re-runnable.

- **Phase 5:** If commit or push fails, fix the issue and retry. If PR creation
fails, create manually.

If a tolerance threshold is breached (e.g., YAML fails validation), stop
immediately and escalate rather than working around the error.

## Artifacts and notes

Key artifacts produced:

1. **docs/quickstart.md** — The main deliverable. Scenario-driven guide with 4
examples, ~800–1,000 lines.

2. **docs/execplans/6-2-1-dependency-matrix.txt** — Clarity on which actions are
always-required vs optional.

3. **Updated README.md** — Added link to quickstart.

4. **Updated AGENTS.md** — Added reference in "Getting Started" section.

5. **Draft PR** — (6.2.1) Write Quickstart Guide for shared-actions, linking to
this execplan.

## Interfaces and dependencies

**External dependencies:**

- GitHub Actions: Users' workflows will `uses` the actions defined in
`.github/actions/`.
- Documentation: Links to existing docs (AGENTS.md, developers-guide.md,
individual action READMEs).

**No new interfaces or dependencies are created by this plan.**

**Validation tools:**

- `make lint` — Runs action-validator to check YAML syntax.
- `act` (local execution tool) — Optional, for smoke testing workflows locally
before CI.
- `grep` / link checkers — For validating documentation links.

---

## Status: DRAFT

This ExecPlan is ready for review and approval. It addresses the 6 expert review
gaps and provides a clear roadmap for writing a scenario-driven quickstart guide
that reduces friction for new users of shared-actions while preserving deep
reference documentation for advanced customization.

To proceed, the user must:

1. Review this execplan and approve or request revisions.
2. Confirm the 4-action scope and Rust-primary positioning.
3. Identify the maintenance owner for quarterly reviews.
4. Approve proceeding to Phase 1.

Upon approval, implementation will proceed milestone-by-milestone with clear
go/no-go gates at each phase.
