# 10.1.3 Approve Actor and Codec-Driver Boundary

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises &
Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be
kept up to date as work proceeds.

Status: DRAFT

## Purpose / big picture

This work turns the `Frame = Vec<u8>` design inventory into an
approved, formal decision about the actor and codec-driver boundary.
The goal is to ensure that `Vec<u8>` bridges leave the core runtime
deliberately rather than incidentally—that is, the boundary between
the application layer (actors) and the transport layer (codec drivers)
is explicit, intentional, and documented.

After this work, stakeholders can point to a clear design decision
explaining:

- Why the boundary exists at this location
- What responsibilities belong to actors vs. codec drivers
- How `Vec<u8>` bridges are used across this boundary
- What zero-copy or serialization contracts are assumed

This enables both public API design and future migration work to
proceed with confidence.

## Constraints

Hard invariants that must hold throughout implementation.

- The design decision must be informed by and reference the Frame
  inventory (`docs/frame-vec-u8-inventory.md`), ADRs 008-010 (as
  applicable), and any prior analysis of transport frame boundaries.
- The approved boundary design must not introduce breaking changes
  to the current runtime unless those changes are explicitly scoped
  and approved as part of separate work items.
- Public API stability guarantees (if any) must be explicitly stated
  in the design document.
- The decision must account for both the happy path (deliberate use)
  and the current incidental paths (unintended uses that may exist
  today).
- All design artifacts (decision document, ADRs, implementation
  details) must pass markdown linting and comply with documentation
  standards in `docs/developers-guide.md`.


## Tolerances (exception triggers)

Thresholds that trigger escalation when breached.

- **Scope**: If the design document requires substantive changes to
  more than 5 architectural files or more than 200 net lines of
  prose/specification, stop and escalate.
- **Ambiguity**: If critical stakeholders (core team members,
  maintainers) disagree on the boundary definition, stop and present
  options with trade-offs before proceeding.
- **Evidence**: If the design cannot be justified by reference to the
  Frame inventory or existing ADRs, stop and acquire the missing
  evidence before finalizing the decision.
- **Iterations**: If the design undergoes more than 3 substantial
  revisions during review, stop and escalate to determine if a larger
  architectural discussion is needed.
- **Dependencies**: If the design requires breaking changes to
  runtime interfaces, stop and escalate.


## Risks

Known uncertainties that might affect the plan.

- **Risk**: The Frame inventory may be incomplete or inaccurate.
  Severity: medium
  Likelihood: medium
  Mitigation: Review the inventory against the current codebase using
  `grep` and `leta refs` to spot incidental uses. Cross-reference with
  ADRs 008-010 if they exist.

- **Risk**: Codec drivers may be distributed across multiple
  files/crates, making the boundary definition ambiguous.
  Severity: medium
  Likelihood: medium
  Mitigation: Use code navigation (leta) to identify all codec driver
  implementations and their dependencies. Document the actual
  boundary, not an idealized one.

- **Risk**: Stakeholders may have different understandings of what
  "deliberately" means in this context.
  Severity: high
  Likelihood: medium
  Mitigation: Engage stakeholders early in the design phase. Clarify
  the distinction between intentional use (by design) and accidental
  use (a bug or debt item) with concrete examples.

- **Risk**: The design may conflict with historical decisions or
  constraints not yet documented.
  Severity: medium
  Likelihood: low
  Mitigation: Review git history for Frame-related commits and PRs.
  Check for design decisions recorded in prior ADRs or issue
  discussions.


## Progress

Use a list with checkboxes to summarise granular steps. Every
stopping point is documented here.

- [ ] Stage 1: Gather and validate existing inventory and ADRs.
- [ ] Stage 2: Draft the boundary design document.
- [ ] Stage 3: Review with domain experts (community of experts
  agent team).
- [ ] Stage 4: Refine design based on feedback.
- [ ] Stage 5: Finalize decision document and ADR updates.
- [ ] Stage 6: Validation gates (lint, format, documentation
  standards).
- [ ] Stage 7: Prepare for implementation (mark as APPROVED, create
  PR).


## Surprises & discoveries

Unexpected findings during implementation that were not anticipated as
risks. This section will be populated as work proceeds.

(To be filled in as work progresses)


## Decision log

Record every significant decision made while working on the plan.

- Decision: (to be filled in)
  Rationale: (to be filled in)
  Date/Author: (to be filled in)


## Outcomes & retrospective

Summarize outcomes, gaps, and lessons learned at major milestones or at completion.

(To be filled in at completion)


## Context and orientation

**Current state**: The codebase has actors that interact with codec
drivers through Frame abstractions. Frames are often represented as
`Vec<u8>` in practice, but the boundary definition between actor
responsibilities and codec-driver responsibilities is not formally
documented or approved.

**Key files and modules**:

- `docs/frame-vec-u8-inventory.md` — inventory of where and how
  `Vec<u8>` is used in the Frame abstraction (to be reviewed or
  created).
- `docs/adr-010-transport-frame-boundary-for-zero-copy.md`
  (referenced, may not exist yet).
- ADRs 008, 009 (referenced, may not exist yet).
- `docs/developers-guide.md` — documentation standards and
  architectural conventions.
- Core runtime modules that define actors and codec driver
  interfaces.

**Who cares**: Stakeholders include the core team (who maintain actor
and codec-driver interfaces), API designers (who will build public
APIs on top of this boundary), and the community (who will implement
custom codec drivers).

**What happens next**: Once approved, this decision document becomes
the north star for:

1. Public API design (e.g., exposing codec-driver traits or Frame
   types).
2. Migration work (e.g., refactoring accidental uses to be
   deliberate).
3. New codec-driver implementations (following the boundary
   contract).


## Plan of work

The work is organized into stages with explicit go/no-go validation
between stages.

### Stage 1: Gather and validate existing inventory

Collect and verify the Frame inventory, existing ADRs, and current
codec-driver implementations. Use code navigation (leta) to identify
actual patterns. Validate that the inventory captures both deliberate
and incidental uses.

**Concrete actions**:

1. Review or create `docs/frame-vec-u8-inventory.md`.
2. Locate and review ADRs 008-010 (or note if they don't exist yet).
3. Use `leta` to identify all codec-driver implementations and
   actor-codec-driver boundaries.
4. Cross-reference the inventory against the codebase to identify
   gaps.

**Validation**: Stakeholders confirm the inventory is complete and
accurate.

### Stage 2: Draft the boundary design document

Write a clear design document that defines:

- The location of the actor/codec-driver boundary (by module/trait/
  file).
- What responsibilities live on each side.
- How `Vec<u8>` crosses the boundary (contract, assumptions,
  performance constraints).
- Examples of deliberate use (intended design) and incidental use
  (technical debt).
- Any migration path if incidental uses exist today.

**Concrete actions**:

1. Create or update the design document at
   `docs/actor-codec-driver-boundary-design.md` (or similar, to be
   confirmed).
2. Include references to the Frame inventory and ADRs.
3. Provide concrete code examples (using `leta show` or direct
   excerpts).
4. State any zero-copy or serialization assumptions.

**Validation**: The document is syntactically correct, passes markdown
linting, and clearly defines the boundary.

### Stage 3: Review with domain experts

Engage a community of experts agent team to review the design from
multiple perspectives (architecture, performance, API surface,
extensibility). Gather structured feedback on:

- Clarity of the boundary definition.
- Completeness (are all cases covered?).
- Compatibility with the existing codebase.
- Alignment with project goals.

**Concrete actions**:

1. Prepare a summary of the design document and key questions for
   reviewers.
2. Use an agent team (with diverse expertise) to independently review
   and provide structured feedback.
3. Document review outcomes and flag any disagreements or concerns.

**Validation**: Reviewers confirm the boundary is well-defined,
technically sound, and ready for decision.

### Stage 4: Refine design based on feedback

Incorporate feedback from domain experts. Update the design document
and ADRs to address concerns. If feedback indicates the boundary
should change, update the definition and re-validate.

**Concrete actions**:

1. Update the design document with clarifications or changes.
2. Update ADRs if they were created or modified.
3. Re-run markdown linting.
4. Confirm stakeholders are aligned on the refined design.

**Validation**: The refined design passes linting, addresses all
reviewer concerns, and is approved by stakeholders.

### Stage 5: Finalize decision document and ADR updates

Ensure all design artifacts are complete, consistent, and ready for
publication. Update any related documentation (e.g.,
`docs/developers-guide.md`, `docs/users-guide.md` if applicable).

**Concrete actions**:

1. Ensure the design document is comprehensive and self-contained.
2. Create or update ADR-010 (or relevant ADR) to record the
   decision.
3. Update `docs/developers-guide.md` with any architectural guidance
   on the boundary.
4. Run all validation gates (lint, format check).

**Validation**: All documentation passes linting, markdown validation,
and is approved.

### Stage 6: Validation gates

Run deterministic gates to ensure the work meets quality standards.

**Concrete actions**:

1. Run `make check-fmt` (or equivalent formatting check).
2. Run markdown linting (ensure compliance with project standards).
3. Verify all files are under version control (no untracked docs).

**Validation**: All gates pass with no errors.

### Stage 7: Prepare for implementation

Mark the plan as APPROVED. Create a PR for the execplan and design
artifacts. Include a link to the Lody session for reference and a
summary of the decision for stakeholders.

**Concrete actions**:

1. Update this execplan: change Status from DRAFT to APPROVED.
2. Commit the design document, ADR updates, and execplan to the
   branch.
3. Push the branch to origin.
4. Create a draft PR with:
   - Title: `(10.1.3) Approve actor and codec-driver boundary`
   - Description: summary of the decision, reference to the design
     document, and Lody session link.
5. Await explicit approval from stakeholders before proceeding to
   implementation.

**Validation**: PR is created, reviews are requested, and stakeholders
confirm the design is ready for implementation.


## Concrete steps

Exact commands to run and where to run them (working directory:
`/tmp/lody-title-agent`).

### Stage 1: Gather and validate

```bash
# Check if the Frame inventory exists
ls -la docs/frame-vec-u8-inventory.md

# Check if ADRs 008-010 exist
ls -la docs/adr/ | grep -E "008|009|010"

# Use leta to search for codec-driver implementations
leta grep "codec.*driver\|CodecDriver" -k class,struct,trait

# Use leta to find Frame and Vec<u8> usages
leta grep "Vec<u8>\|Frame" -k function,method --head 100
```

### Stage 2: Draft the design document

Create the file: `docs/actor-codec-driver-boundary-design.md`

The document should follow the structure:

- Executive summary (1-2 paragraphs)
- The boundary definition (modules, traits, responsibility split)
- How `Vec<u8>` crosses the boundary
- Assumptions and constraints
- Examples (deliberate and incidental)
- Migration path (if needed)
- References (inventory, ADRs)

### Stage 3-4: Review and refine

Use an agent team to review the draft. Expect 1-3 rounds of
refinement.

### Stage 5-6: Finalize and validate

```bash
# Lint check
make lint 2>&1 | tee /tmp/lint-10-1-3.out

# Format check
make check-fmt 2>&1 | tee /tmp/fmt-10-1-3.out
```

### Stage 7: Create PR

```bash
# Commit the design work
git add \
  docs/execplans/10-1-3-approve-actor-and-codec-driver-boundary.md \
  docs/actor-codec-driver-boundary-design.md \
  docs/adr/010-transport-frame-boundary-for-zero-copy.md

git commit -m \
  "docs(10.1.3): Formulate actor-codec-driver boundary design"

# Push to tracking branch
git push -u origin \
  10-1-3-approve-actor-and-codec-driver-boundary
```


## Validation and acceptance

### Red-Green-Refactor (Design Document Edition)

Since this is a design decision rather than code, Red-Green-Refactor
takes the form of a specification-based review:

1. **Red**: The boundary is currently implicit and undefined.
2. **Green**: The boundary is now formally defined in a design
   document, reviewed by experts, and approved.
3. **Refactor**: Any follow-up implementation work (migration of
   incidental uses, public API design) is scoped separately.

### Quality criteria

- Design document is complete, clear, and self-contained.
- All stakeholders agree on the boundary definition.
- No ambiguity remains about actor vs. codec-driver
  responsibilities.
- Documentation passes markdown linting.
- ADR(s) are up-to-date and reference the design decision.

### Validation commands

```bash
# Markdown linting
make lint 2>&1 | tee /tmp/lint-final.out

# Format check
make check-fmt 2>&1 | tee /tmp/fmt-final.out

# Confirm files are tracked and committed
git status
git log --oneline | head -5
```

### Acceptance

The design is accepted when:

1. The design document passes linting and markdown validation.
2. Stakeholders have reviewed and approved the boundary definition.
3. The PR is created and awaits final merge/approval.


## Idempotence and recovery

All steps are idempotent:

- Re-running `leta grep` commands will produce the same results.
- The design document can be revised and re-linted without side
  effects.
- Commits can be amended (before push) or new commits added (after
  push) to address feedback.

If a stage fails:

1. Document the failure in `Surprises & Discoveries`.
2. Escalate if the failure touches a tolerance threshold.
3. Otherwise, fix the issue and retry the stage validation.


## Artifacts and notes

Key artifacts produced by this plan:

1. **Design Document**: `docs/actor-codec-driver-boundary-design.md`
   - Defines the actor/codec-driver boundary.
   - Includes Frame abstraction contract and use cases.
   - Contains code examples and assumptions.

2. **Updated ADRs**: `docs/adr/010-*.md` (and others as needed)
   - Records the formal decision.
   - Explains the rationale for the boundary location.
   - References prior analysis and trade-offs.

3. **ExecPlan**: This document
   (`docs/execplans/10-1-3-approve-actor-and-codec-driver-boundary.md`)
   - Guides the work from design to approval.
   - Tracks progress and decisions.

4. **PR**: Draft PR with design artifacts and link to Lody session.


## Interfaces and dependencies

The design decision affects the following interfaces (to be clarified
during Stage 2-3):

- Actor trait (if one exists) — what methods relate to Frame
  handling?
- CodecDriver trait (if one exists) — what is the Frame contract?
- Frame abstraction itself — what assumptions does it make about
  ownership, lifetime, and `Vec<u8>` representation?

The decision will inform (but not implement):

- Public API design for codec-driver trait publication.
- Migration plan for any incidental uses that should become
  deliberate.
- Testing strategy for boundary contracts.

All design work must be fully documented and approved before
implementation begins on the separate work item.
