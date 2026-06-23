# 1.1.1 Record the Transition-Boundary Scope Decision as an ADR

This ExecPlan is a living document. The sections `Constraints`, `Tolerances`,
`Risks`, `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes &
Retrospective` must be kept up to date as work proceeds.

Status: DRAFT


## Purpose / big picture

This work records a foundational architectural decision for the Statelet project
v0.1: what boundaries the crate owns and what it explicitly does not own.

The Statelet crate will be a boundary-marking and state validation library that
identifies state transitions in a domain without owning the machinery of those
transitions. After this ADR is accepted, the project will have a documented
contract stating that Statelet marks transitions but does not own dispatch,
events, storage, transition tables, or graph safety.

This decision is foundational because it enables later work (Phase 1.2+) to
build concrete implementations within a well-defined scope without ambiguity.
Success is observable when:

1. An ADR document exists at `docs/adr/0001-statelet-ownership-boundary.md`
2. The ADR is reviewed and approved by at least one domain expert
3. The ADR states with clarity that Statelet:
   - *Does* mark transition boundaries
   - *Does not* own dispatch logic
   - *Does not* own event creation, propagation, or routing
   - *Does not* own state storage or persistence
   - *Does not* own transition table definition or compilation
   - *Does not* own graph safety properties or cycle detection
4. The decision is cross-referenced in `docs/design.md`
5. `make lint` passes on the ADR document


## Constraints

Hard invariants that must hold throughout this work.

- **No implementation code added**: This milestone is documentation and decision
  only. No Rust code, no tests, no feature additions in this ExecPlan.
- **ADR format correctness**: The ADR must follow the project's ADR template and
  style guide, as documented in `docs/adr/README.md` (if it exists).
- **Scope fidelity**: The documented scope must align with
  `docs/terms-of-reference.md` §§1-6 and `docs/design.md` §§1-3. If sources
  contradict this plan, escalate rather than guessing.
- **No unilateral design changes**: The ADR records decisions already made
  (during terms-of-reference and design phases), not new decisions. If writing
  the ADR reveals design gaps, document them in the Decision Log and escalate.
- **Markdown linting**: The final document must pass
  `make lint` without errors.


## Tolerances (exception triggers)

Thresholds that trigger escalation when breached.

- **Scope ambiguity**: If the terms-of-reference.md or design.md sources are
  unavailable or contradictory, stop and escalate with evidence. Do not invent
  scope.
- **Expert review availability**: If no domain expert (at minimum, the original
  design author or project stakeholder) is available for review within 48 hours,
  document this and proceed with best-effort documentation, flagging the gap.
- **ADR length**: If the ADR exceeds 2000 words without prior approval, stop and
  present for scope review.
- **Linting failures**: If the final document fails `make lint`, fix and retry.
  If lint issues require design changes, escalate.
- **Cross-reference conflicts**: If updating `docs/design.md` requires changes
  incompatible with the ADR, escalate with evidence of the conflict.


## Risks

Known uncertainties that might affect the plan.

- **Risk**: The terms-of-reference.md and design.md documents may not exist,
  be incomplete, or have been moved.
  Severity: medium
  Likelihood: medium (14 days old memory, project may have evolved)
  Mitigation: Search for key documents early; if absent, ask user for their
  locations. Do not proceed without understanding the source scope.

- **Risk**: Scope boundaries may be implicit in the design rather than explicit.
  Severity: medium
  Likelihood: medium (design docs often leave ownership implicit)
  Mitigation: When writing the ADR, explicitly list what Statelet *does not*
  own, forcing clarity. Escalate if ambiguity remains.

- **Risk**: Expert reviewer may request significant rewrites.
  Severity: low
  Likelihood: low (scope was decided in prior phases)
  Mitigation: Treat reviewer feedback as clarification, not redesign. If
  feedback contradicts prior decisions, document in Decision Log and escalate.

- **Risk**: ADR format expectations may differ from what the project uses.
  Severity: low
  Likelihood: low (standard ADR templates are well-established)
  Mitigation: Check for existing ADRs in `docs/adr/` and mirror their structure.


## Progress

Checkpoint list. Timestamps mark progression and help detect tolerance
breaches.

- [ ] Phase 0: Locate source documents and verify context
- [ ] Phase 1: Draft ADR title, motivation, and constraints
- [ ] Phase 2: Write "what Statelet owns" and "what it does not own"
- [ ] Phase 3: Document implications for downstream architecture
- [ ] Phase 4: Get expert review (domain expert or project stakeholder)
- [ ] Phase 5: Revise based on feedback and finalize
- [ ] Phase 6: Update `docs/design.md` cross-references
- [ ] Phase 7: Lint validation and cleanup
- [ ] Phase 8: Create draft PR and await approval before roadmap update


## Surprises & Discoveries

Unexpected findings during work. To be updated as plan proceeds.

(None yet.)


## Decision Log

Significant decisions made while working on this plan.

(None yet; initial decisions occur in Phase 0 context gathering.)


## Outcomes & Retrospective

Results, gaps, and lessons learned. To be updated at major milestones and at
completion.

(To be filled in upon completion.)


---


## Context and Orientation

**Current state**: The Statelet project is in Phase 1 (foundational contracts
and kill gates). Phases 1.1.1 through 1.2+ define what the crate will and will
not do before any runtime code is written.

**Key files** (to be verified in Phase 0):
- `docs/terms-of-reference.md` — rationale and stakeholder requirements
- `docs/design.md` — high-level design sketches and decisions
- `docs/adr/` — existing ADR collection (to understand local conventions)
- `docs/developers-guide.md` — internal conventions and architecture overview
- `docs/users-guide.md` — user-facing API and behaviour documentation

**ADR scope**: This ADR documents *ownership boundaries*, not implementation
details. It answers "What does Statelet own?" and "What does it not own?" at
the crate level, providing a scope gate for all later work.

**Audience**: Internal stakeholders, future contributors, and anyone evaluating
whether Statelet is suitable for their use case.


## Plan of Work

### Phase 0: Context Gathering (no edits, decision only)

Locate and verify the source documents that define the scope decision:
- `docs/terms-of-reference.md` §§1-6 (stakeholder requirements)
- `docs/design.md` §§1-3 (design overview) and §§11.1, 13.6-13.7 (scope sketches)

If these documents exist and are complete, proceed to Phase 1. If any are
missing or unclear, escalate with the missing pieces. This is a gate: do not
proceed to ADR writing without understanding the source scope.

**Decision point**: Does the source material exist and provide clear scope
boundaries? If not, escalate.

### Phase 1: ADR Skeleton and Motivation

Create `docs/adr/0001-statelet-ownership-boundary.md` with:
1. Title: "Statelet marks transition boundaries but does not own dispatch,
   events, storage, transition tables, or graph safety"
2. Status section (initially DRAFT, pending review)
3. Context section (2–3 sentences on what problem Statelet solves)
4. Decision section (what Statelet will and will not do)
5. Consequences section (implications for dependent code)

The decision section must include:

**Statelet owns:**
- Defining and marking state transition boundaries in domain code
- Providing types and traits to identify transitions and validate their
  well-formedness
- Integrating with user-provided dispatch, storage, and event routing

**Statelet does not own:**
- Dispatch logic (routing transitions to handlers)
- Event creation, propagation, or serialization
- State storage or persistence mechanisms
- Transition table definition, compilation, or optimization
- Graph safety properties (cycle detection, liveness, etc.)
- Concurrency control or synchronization primitives

### Phase 2: Write Consequences and Implications

Expand the Consequences section to describe:
1. What dependent code must provide (dispatch mechanism, event types, storage)
2. What patterns Statelet supports (state machines, workflow engines, etc.)
3. What patterns Statelet does not support or leaves to user code (distributed
   consensus, transactional rollback, etc.)
4. How this scope boundary affects testing, composition, and library updates

### Phase 3: Domain Expert Review

Submit the draft ADR for review by at least one stakeholder. The reviewer should
verify:
- Scope boundaries match the original intent
- No critical ownership is missing or misassigned
- Wording is clear to someone new to the project

Collect feedback. If changes are requested, revise Phases 1–2 and re-review.

**Decision point**: Is the ADR approved for inclusion in the codebase? If
feedback requests substantial rewrites, document in Decision Log and escalate.

### Phase 4: Update Design Cross-References

Edit `docs/design.md` to add a cross-reference to the new ADR:
```plaintext
## Scope and Ownership

See ADR 0001: Statelet marks transition boundaries but does not own dispatch,
events, storage, transition tables, or graph safety. This ADR documents the
foundational ownership boundary that shapes all downstream work.
```

Place this near the top of `docs/design.md` (in the overview or introduction
section).

### Phase 5: Lint and Finalize

Run `make lint` to validate the ADR document. Fix any violations (line length,
heading structure, etc.). Ensure the document passes all style checks before
marking complete.

### Phase 6: Create Draft PR

Rename the current git branch (if needed) to track the task:
```bash
git branch -m 1-1-1-record-the-transition-boundary-scope-decision-as-an-adr
```

Commit the ADR and cross-reference:
```bash
git add docs/adr/0001-statelet-ownership-boundary.md docs/design.md
git commit -m "docs(1.1.1): Record transition-boundary scope decision as ADR"
```

Push to the remote and create a draft PR:
```bash
git push -u origin 1-1-1-record-the-transition-boundary-scope-decision-as-an-adr
```

Create the PR with title: `(10.1.3) Record transition-boundary scope decision as ADR`

Include the lody session link in the PR description (to be obtained from
environment variable `${LODY_SESSION_ID}`).


## Concrete Steps

### Step 1: Verify source documents exist

From the project root:

```bash
ls -la docs/terms-of-reference.md docs/design.md
```

Expected output:
```
-rw-r--r-- ... docs/terms-of-reference.md
-rw-r--r-- ... docs/design.md
```

If either file is missing, escalate immediately with the missing file paths.

### Step 2: Review ADR conventions

Check for existing ADRs to understand format:

```bash
ls docs/adr/
```

Expected: one or more `.md` files with filenames like `0001-*.md`, `0002-*.md`,
etc.

Read the first ADR to understand the local format. If a `docs/adr/README.md`
exists, read that too.

### Step 3: Create the ADR file

Using the skeleton in Phases 1–2, write:

```plaintext
docs/adr/0001-statelet-ownership-boundary.md
```

Content must include the decision statement, context, consequences, and
ownership lists from Phase 1.

### Step 4: Lint check

```bash
make lint
```

Expected: No errors. If errors occur, read the lint output, fix formatting
issues, and retry.

### Step 5: Commit and push

```bash
git add docs/adr/0001-statelet-ownership-boundary.md docs/design.md
git commit -m "docs(1.1.1): Record transition-boundary scope decision as ADR"
git push -u origin $(git branch --show-current)
```

### Step 6: Create PR

From the GitHub UI or via `gh`:

```bash
LODY_SESSION_ID="<session-id-from-env>"
gh pr create \
  --title "(10.1.3) Record transition-boundary scope decision as ADR" \
  --body "## Summary

Documents the foundational ownership boundary for Statelet v0.1.

See ADR 0001 for the scope decision: Statelet marks transition boundaries but
does not own dispatch, events, storage, transition tables, or graph safety.

## References

- [Lody Session](https://lody.ai/leynos/sessions/${LODY_SESSION_ID})" \
  --draft
```


## Validation and Acceptance

**Quality criteria**:

1. The ADR file exists and is valid Markdown.
2. The ADR clearly states what Statelet owns and does not own.
3. The document passes `make lint` without errors.
4. The document has been reviewed by at least one domain expert.
5. The cross-reference in `docs/design.md` exists and points to the ADR.
6. A draft PR exists with the ADR and design updates.

**How to check**:

```bash
# Verify file exists
test -f docs/adr/0001-statelet-ownership-boundary.md && echo "✓ ADR file exists"

# Run lint
make lint
# Expected: no errors

# Verify cross-reference
grep -q "ADR 0001" docs/design.md && echo "✓ Cross-reference exists"

# Check git status
git status --short
# Expected: both files staged and committed
```

**Acceptance**: All quality criteria pass, expert review is complete (feedback
incorporated), and the draft PR is awaiting approval.


## Idempotence and Recovery

Creating or editing the ADR is idempotent: re-running the steps will either
create the file afresh or update an existing draft without loss. If the PR is
created and later rejected, simply update the document on the branch and
re-push; GitHub will update the PR.

If lint fails and you need to retry:
1. Fix the reported lint issue.
2. Run `make lint` again to verify.
3. Stage, commit, and push the fix.

If the expert reviewer requests changes:
1. Update the ADR and design.md with feedback.
2. Commit and push (same branch).
3. Notify the reviewer of updates.


## Artifacts and Notes

### Expected ADR Skeleton

```plaintext
# ADR 0001: Statelet Marks Transition Boundaries

## Status

DRAFT (pending expert review)

## Context

Statelet is a lightweight boundary-marking and state validation library for
domain-driven state machines. The project scope must be clear from the outset
to prevent scope creep and to guide the design of v0.1 and later releases.

## Decision

Statelet will provide types and traits to mark and validate state transition
boundaries. It will not provide dispatch logic, event handling, storage, or
graph safety guarantees.

### What Statelet Owns

- Type-safe boundary marking for state transitions
- Integration hooks for user-provided dispatch, events, and storage
- Documentation and examples of common composition patterns

### What Statelet Does Not Own

- Dispatch logic or transition routing
- Event creation, serialization, or propagation
- State storage, caching, or persistence
- Transition table definition or compilation
- Graph properties (cycles, liveness, reachability)
- Concurrency, synchronization, or distributed coordination

## Consequences

...
```

### Example Cross-Reference Update

In `docs/design.md`, add near the top:

```markdown
## Scope and Ownership

See [ADR 0001: Statelet Marks Transition Boundaries](./adr/0001-statelet-ownership-boundary.md)
for the foundational scope decision that shapes all architecture and API
design. This boundary is enforced in all phases.
```

---

## Interfaces and Dependencies

**No new interfaces or code changes are required.** This is a documentation
milestone. The ADR document itself is the artifact.

**Dependencies**:
- The project's `make lint` command must work correctly.
- Markdown linting rules must be consistent with existing documentation.
- Domain experts or stakeholders must be available for review within
  48 hours.

---

## Revision Note

(None yet. Initial draft.)

---

**Next Steps After Approval**

Once this ADR is approved and the draft PR is reviewed:
1. Merge the PR (with sign-off from stakeholders).
2. Mark the roadmap entry (10.1.3) as "done" in `docs/roadmap.md`.
3. Proceed to task 1.1.2 (ratify kill-gate criteria) or 1.2+ as planned.
