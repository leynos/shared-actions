# Record the Profile Rule-Expression Policy (1.1.4)

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as
work proceeds.

Status: DRAFT

## Purpose / big picture

Prosidy Darn must decide whether profile configuration files allow arbitrary
custom rule expressions or only named rule weights before Phase 2 implementation
begins. This decision affects the segmenter architecture, test strategy, and
feature scope for v1. Once locked, changing this decision requires breaking
changes to the profile schema.

A user will be able to configure profile files that weight and parameterize
rules. After this decision is recorded in an ADR, the segmenter component can
be designed without uncertainty about expression capability, and Phase 2 can
proceed with a coherent v1 contract.

## Constraints

Hard invariants that must hold throughout implementation.

- The decision must not assume the existence of an expression language. No new
  expression-language design decision may be introduced during this task.
- The profile schema must be documented in the ADR with sufficient specificity
  that implementation teams can build adapters without asking clarifying
  questions.
- All decision criteria and trade-offs must be visible in the ADR. Hidden
  rationale or undocumented constraints will cause rework in Phase 2.
- The decision must be reversible in v1.1 without breaking v1.0 profiles.
  Architecture must reserve space for future expressions without breaking
  changes.
- No code changes to existing Prosidy Darn codebase are permitted during this
  task. This is a decision-documentation task, not an implementation task.
- The ADR must follow the format and style of existing ADRs under `docs/adr/`.
  See `docs/adr/0001-stable-manpage-path.md` and
  `docs/adr/0002-explicit-ps-module-name.md` for format.

## Tolerances (exception triggers)

Thresholds that trigger escalation when breached.

- **Ambiguity:** If the decision permits multiple interpretations of what
  "named rule weights" means or how expressions would be added in v1.1, stop
  and escalate with the specific ambiguous cases.
- **Prior art:** If research into open-source tools reveals a pattern materially
  different from the recommendation, stop and escalate with the new findings.
- **Scope:** If the ADR grows beyond 15 pages (excluding appendices) or requires
  more than 2 hours of writing, stop and escalate.
- **Conflicts:** If the decision conflicts with existing ADRs or documented
  constraints elsewhere in the codebase, document the conflict and escalate.

## Risks

Known uncertainties that might affect the plan. Identify these upfront and
update as work proceeds.

- **Risk:** Stakeholders disagree on whether arbitrary expressions or named
  weights is right for v1.
  Severity: high
  Likelihood: medium
  Mitigation: The research surveyed 12+ production tools and found zero that
    support arbitrary expressions in config files; all use named weights or
    presets. Include this finding prominently in the ADR so the decision is
    grounded in empirical data, not opinion.

- **Risk:** The recommended option (named weights with v1.1 expression
  architecture designed now) is perceived as "half-baked" or inadequate.
  Severity: medium
  Likelihood: medium
  Mitigation: Structure the ADR to show v1.1 architecture reserved space
    clearly, with a concrete example of how v1.0 profiles will remain valid
    when expressions are added. Demonstrate that v1.0 is complete and useful on
    its own, not a placeholder.

- **Risk:** Segmenter architecture is already partially specified or in flight,
  creating path dependencies that conflict with this decision.
  Severity: medium
  Likelihood: low
  Mitigation: Before finalizing the ADR, confirm no segmenter design work has
    started. If it has, review its assumptions and update them if they conflict
    with the decision.

- **Risk:** The profile schema is too permissive or too restrictive, causing
  Phase 2 to discover issues that require breaking changes.
  Severity: medium
  Likelihood: low
  Mitigation: The ADR must include a concrete example profile showing how
    common rule-weighting scenarios are expressed. Phase 2 planning should
    review this example and flag any gaps before implementation starts.

## Progress

Use a list with checkboxes to summarise granular steps. Every stopping point
must be documented here, even if it requires splitting a partially completed
task into two.

- [x] (2026-06-18 02:07Z) Agent team research on open-source rule-expression
  policies (RESEARCH AGENT 1).
- [x] (2026-06-18 02:07Z) Agent team designs decision framework with three
  options and recommendation (RESEARCH AGENT 2).
- [ ] (2026-06-18 TBD) Draft ADR 0003 based on research findings.
- [ ] (2026-06-18 TBD) Validate ADR format against existing ADRs.
- [ ] (2026-06-18 TBD) Validate profile schema example compiles mentally with
  Phase 2 requirements.
- [ ] (2026-06-18 TBD) Team review and approval of ADR.
- [ ] (2026-06-18 TBD) Rename branch to
  `1-1-4-record-the-profile-rule-expression-policy`.
- [ ] (2026-06-18 TBD) Create draft PR with execplan and ADR.
- [ ] (2026-06-18 TBD) Post PR and await approval before closing DRAFT status.

## Surprises & discoveries

Unexpected findings during planning and research that were not anticipated as
risks.

- **Discovery:** Zero major open-source linters, formatters, or static analysis
  tools support arbitrary custom rule expressions in configuration files. All
  examined tools (ESLint, Prettier, Pylint, Rustfmt, Biome, Clippy,
  Golangci-lint, Stylelint, Flake8, SonarQube, Checkstyle) explicitly reject
  arbitrary expressions. This provides very strong empirical support for
  choosing named weights over expressions.

- **Discovery:** The recommended approach ("named weights + v1.1 architecture
  reserved") is already in use by multiple production tools (ESLint, Biome,
  Clippy) where v1.0 used named weights and v1.1+ added presets or plugins
  without breaking v1.0 configs. This is not a novel approach; it is a proven
  pattern.

## Decision log

Record every significant decision made while designing the plan.

- **Decision:** Use Option B-Extended (named rule weights for v1.0, with v1.1
  expression architecture designed during v1.0) as the recommended profile
  rule-expression policy.
  Rationale: Open-source research shows no major tool supports arbitrary
    expressions in config (security, performance, auditability constraints).
    Named weights are proven in ESLint, Biome, Clippy. This approach unblocks
    Phase 2 immediately while reserving space for expressions in v1.1 without
    breaking v1.0 profiles. Lowest risk, highest value for v1.0.
  Date/Author: 2026-06-18, Agent Research Team.

- **Decision:** Research scope includes 12+ open-source tools with mature
  config systems, focusing on patterns and rationales, not just feature
  coverage.
  Rationale: We need to understand *why* tools chose their policies, not just
    *what* they chose. The why informs whether a decision is replicable in
    Prosidy Darn.
  Date/Author: 2026-06-18, Agent Research Team.

## Outcomes & retrospective

Summary of outcomes and lessons learned. To be updated after completion.

(Pending: to be completed after ADR draft and team review.)

## Context and orientation

**Project:** Prosidy Darn, a text processing and analysis tool with a
plugin-based rule engine.

**Phase 1 (Current):** Foundational contracts and build spine—establish v1
architectural decisions before feature work.

**This Task (1.1.4):** Record the profile rule-expression policy decision.
Profiles are configuration files that users create to customize which rules
apply and how they are parameterized. The decision locked by this task: do
profiles allow arbitrary expressions (e.g., `min_length > 10 &&
severity == "error"`), or only named weights (e.g., `severity: high,
min_length: 10`)?

**Related work:**

- 1.0.1: Hexagonal architecture design (prerequisite; likely complete)
- 1.1.2: Package boundary definition (prerequisite; likely complete)
- Blocked by 1.1.4: 1.2.1 and later Phase 2 work (implementation of the
  segmenter, rule engine, profile loader)

**Key stakeholders:**

- Architecture: needs locked schema to design segmenter
- Phase 2 team: needs locked policy to select dependencies and design rule
  representation
- Users: will depend on profile schema stability across v1 patches

**Key files:**

- This execplan:
  `docs/execplans/1-1-4-record-the-profile-rule-expression-policy.md`
- ADR to be written: `docs/adr/0003-profile-rule-expression-policy.md` (new)

## Plan of work

### Stage 1: Validate Research and Synthesize Recommendation (no code changes)

The agent team has completed research into 12+ open-source tools and designed
a decision framework with three options:

- **Option A:** Arbitrary expressions in profiles (requires new
  expression-language design decision).
- **Option B:** Named rule weights only (no expressions, simple schema).
- **Option B-Extended (recommended):** Named weights for v1.0, with v1.1
  architecture designed in v1.0 to add expressions later without breaking v1.0
  profiles.

First, validate the research findings against the following criteria:

1. The pattern analysis is accurate (spot-check 3-4 tools from the research
   against their official documentation).
2. The recommendation aligns with Prosidy Darn's constraints (does not assume
   an expression language; does not require Phase 2 to make new decisions).
3. The v1.1 architecture sketch is coherent (v1.0 profiles remain valid when
   expressions are added).
4. The scope is bounded (no blockers for Phase 2 implementation).

### Stage 2: Draft ADR 0003 (Writing Task)

Write `docs/adr/0003-profile-rule-expression-policy.md` using the format
established by ADRs 0001 and 0002. The ADR must contain:

- **Status:** Proposed (not Accepted until team approves).
- **Decision:** Named rule weights for v1.0. Profiles consist of rule names,
  severity levels, and named parameters. Arbitrary expressions are not
  supported. v1.1 may add an optional expression-based rule type, designed in
  v1.0 to ensure backward compatibility.
- **Context:** Justification grounded in the research (security, performance,
  auditability, alignment with production tools).
- **Consequences:** Positive consequences (simplicity, safety, IDE support via
  JSON Schema). Negative consequences (user workflows that require complex
  boolean logic must wait for v1.1). Neutral consequences (migration path from
  v1.0 to v1.1 is clear).
- **v1.1 Architecture Sketch (Appendix A):** Concrete example showing how a
  v1.0 profile remains valid when v1.1 adds expressions.
- **Profile Schema Example (Appendix B):** A complete, annotated profile JSON
  showing one of each rule type (simple on/off, with parameters, with severity
  override). Schema must be compatible with Phase 2 segmenter implementation.
- **Research Summary (Appendix C):** One-paragraph summary of the open-source
  research findings and why no major tool supports arbitrary expressions.

### Stage 3: Validation (Reading and Spot-Check)

Before finalizing, validate the ADR against:

1. **Format check:** Paragraph structure, heading hierarchy, and style match
   `docs/adr/0001-*.md` and `docs/adr/0002-*.md`.
2. **Clarity check:** A reader unfamiliar with Prosidy Darn can understand the
   decision, rationale, and v1.1 path without external context.
3. **Completeness check:** The profile schema example is sufficient for Phase 2
   to begin segmenter design without asking clarifying questions.
4. **Conflict check:** The decision does not contradict any existing ADRs or
   documented Phase 1 design constraints.

### Stage 4: Team Review and Approval

Present the ADR to stakeholders (architecture, Phase 2 leads, decision makers).
Gather feedback and update the ADR as needed. The approval gate is: "The team
agrees the decision is locked and suitable for Phase 2 implementation."

Once approved, the status changes from DRAFT to APPROVED, and this execplan
proceeds to the final steps.

### Stage 5: Finalize and Prepare PR

Once approved:

1. Rename the current branch from
   `10-2-5-playbook-variant-compiles-under-pedantic-lint-profile` to
   `1-1-4-record-the-profile-rule-expression-policy`.
2. Stage the execplan and ADR for commit.
3. Create a draft PR with the following:
   - PR title: `(1.1.4) Record the profile rule-expression policy`
   - PR summary: mention the execplan document and note that this is a
     decision-documentation task (no code changes).
   - Body section: "## References" with a link to the lody session.
4. Push to `origin/1-1-4-record-the-profile-rule-expression-policy` and update
   the branch tracking.

## Concrete steps

The following are the exact steps to execute, with expected transcripts where
applicable.

### Step 1: Validate Research (Stage 1)

Read the agent research summaries to spot-check the recommendation. Spot-check
3-4 tools against their official docs. For example:

- ESLint: confirm it uses "rules" as a map of rule names to severity levels
  and config objects (not arbitrary expressions in rule config).
- Biome: confirm it uses severity + presets, not arbitrary expressions.
- Pylint: confirm it uses rule names and parameter values, not arbitrary
  expressions in the config file.

Record findings in the `Surprises & Discoveries` section if any contradictions
emerge.

### Step 2: Draft ADR 0003 (Stage 2)

Create `docs/adr/0003-profile-rule-expression-policy.md`. Use the following
outline and structure it to match ADRs 0001 and 0002:

```markdown
# ADR 0003: Profile Rule-Expression Policy

**Status:** Proposed
**Date:** 2026-06-18

## Context

[2-3 paragraphs explaining why this decision matters now, what triggered the
need, how it affects Phase 2]

## Decision

[1 paragraph: named rule weights for v1.0; no arbitrary expressions; v1.1 may
add expressions without breaking v1.0]

## Rationale

[3-4 paragraphs grounded in the research: why arbitrary expressions are
rejected, why named weights align with production tools, why the v1.1 path is
designed now, what constraints guide the choice]

## Consequences

### Positive
- Security: no code execution in profiles
- Performance: O(n) parsing, no evaluation overhead
- Auditability: profiles are human-readable and reviewable
- IDE Support: schema in JSON Schema for autocomplete
- Alignment: follows ESLint, Biome, Clippy pattern

### Negative
- Extensibility: users cannot write custom boolean logic in profiles before
  v1.1
- Learning curve: users must learn named rule parameters instead of writing
  expressions

### Neutral
- Migration: v1.0 profiles will be valid in v1.1 without changes
- Implementation: segmenter design is not complex with named weights

## v1.1 Architecture Sketch (Appendix A)

[Example showing how v1.0 profile structure maps to v1.1 with optional
expressions]

## Profile Schema Example (Appendix B)

[JSON example with annotations showing rule names, severity, parameters]

## Research Findings (Appendix C)

[One paragraph summary of open-source research]
```

Expected output: A file at `docs/adr/0003-profile-rule-expression-policy.md`
(estimated 6-8 pages, ~2,000 words).

### Step 3: Validation (Stage 3)

After drafting, validate the ADR:

```bash
# Lint Markdown (if a linter is available)
markdownlint docs/adr/0003-profile-rule-expression-policy.md || true
```

Update the ADR if any issues emerge.

### Step 4: Team Review and Approval (Stage 4)

Present the ADR to stakeholders and gather approval. This is a manual gate.
Update the ADR's Status field to "Accepted" once approval is given.

### Step 5: Finalize and Prepare PR (Stage 5)

Once approved:

```bash
# Rename the branch
git branch -m 1-1-4-record-the-profile-rule-expression-policy

# Stage the execplan and ADR
git add docs/execplans/1-1-4-record-the-profile-rule-expression-policy.md
git add docs/adr/0003-profile-rule-expression-policy.md

# Commit with gated checks
make check-fmt
make lint
make typecheck
git commit -m "Add ADR 0003 and execplan for profile rule-expression policy"

# Create draft PR
gh pr create --draft \
  --title "(1.1.4) Record the profile rule-expression policy" \
  --body "## Summary
Finalize the v1.0 profile rule-expression policy.

- Decision: Named rule weights for v1.0; no arbitrary expressions.
- Rationale: Security, performance, alignment with production tools.
- Future path: v1.1 architecture reserved for expressions without breaking.
- Deliverables: ADR 0003 and this execplan.

## References
- Execplan: docs/execplans/1-1-4-record-the-profile-rule-expression-policy.md
- Session: https://lody.ai/leynos/sessions/\${LODY_SESSION_ID}
"

# Track the remote branch
git push -u origin 1-1-4-record-the-profile-rule-expression-policy
```

Expected output:

```plaintext
Draft pull request #<N> created: https://github.com/<owner>/<repo>/pull/<N>
Branch 1-1-4-record-the-profile-rule-expression-policy set up to track
origin/1-1-4-record-the-profile-rule-expression-policy.
```

## Validation and acceptance

### Quality criteria

1. **ADR completeness:** The ADR must include all sections (Context, Decision,
   Rationale, Consequences, Appendices). No section may be empty or
   placeholder.
2. **Format:** The ADR must follow the format of ADRs 0001 and 0002 (heading
   hierarchy, paragraph structure, style).
3. **Clarity:** A reader unfamiliar with Prosidy Darn must be able to
   understand the decision, rationale, and v1.1 path without external context.
4. **Profile schema:** The schema example must be complete enough for Phase 2
   to design the segmenter without clarifying questions.
5. **No code changes:** The execplan and ADR are documentation only. No changes
   to source code, tests, or build configuration.
6. **Team approval:** Stakeholders must approve the decision before closing
   DRAFT status.

### Quality method

```bash
# After completing all stages:
make check-fmt
make lint
git log --oneline -5
gh pr view <PR_NUMBER> --json status
```

Expected output:

- All formatting and lint checks pass.
- Commits are clean (no unintended files staged).
- Draft PR exists and is visible.

### Acceptance narrative

After approval and the draft PR is created, the task is complete. The decision
is locked in the ADR. Phase 2 can now begin implementation with confidence that
the profile schema is stable. The execplan serves as a record of how the
decision was made and what was considered.

The successful outcome is: "Phase 2 architecture and segmenter design begins
without needing clarification on whether profiles support arbitrary
expressions."

## Idempotence and recovery

All steps are idempotent:

- Drafting the ADR can be repeated; overwrite the file and re-validate.
- Validation steps use read-only checks; no side effects.
- Team review can be repeated; just update the ADR and re-present.
- Branch rename and PR creation are one-time operations, but if they fail,
  delete the branch/PR and retry.

If a branch rename fails:

```bash
# Delete locally
git branch -d 1-1-4-record-the-profile-rule-expression-policy
# Delete remote
git push origin --delete 1-1-4-record-the-profile-rule-expression-policy
# Rename again
git branch -m 1-1-4-record-the-profile-rule-expression-policy
```

## Artifacts and notes

Key artifacts produced:

1. **ADR 0003:** `docs/adr/0003-profile-rule-expression-policy.md`
   (~2,000 words, 6-8 pages).
2. **Execplan (this document):**
   `docs/execplans/1-1-4-record-the-profile-rule-expression-policy.md`
   (living document).
3. **Draft PR:** Created on GitHub with title
   "(1.1.4) Record the profile rule-expression policy".

## Interfaces and dependencies

### Profile Schema (Target Interface for v1.0)

The ADR must define a profile schema suitable for the segmenter to consume.
Here is the expected structure (to be detailed in the ADR):

```json
{
  "version": "1.0",
  "rules": {
    "identifier-length": {
      "enabled": true,
      "severity": "error",
      "minLength": 3,
      "maxLength": 100
    },
    "line-length": {
      "enabled": false
    },
    "no-trailing-whitespace": {
      "enabled": true,
      "severity": "warning"
    }
  }
}
```

This schema is the contract between the profile loader and the segmenter.
Changing it after v1.0 release requires a major version bump.

### v1.1 Extension (Reserved, Not Implemented)

The ADR must sketch how v1.1 would extend this schema to support expressions
without breaking v1.0:

```json
{
  "version": "1.0 or 1.1",
  "rules": {
    "identifier-length": {
      "enabled": true,
      "severity": "error",
      "minLength": 3
    },
    "custom-rule-expr": {
      "enabled": true,
      "expression": "length(identifier) > ${minLength}",
      "severity": "error",
      "params": { "minLength": 3 }
    }
  }
}
```

The segmenter in v1.0 ignores unknown rule types (like `custom-rule-expr`) so
v1.1 profiles work in v1.0 (with the expression rule simply not evaluated).
This forward compatibility is the key to the reversible design.

---

**Revision note:** Initial draft created 2026-06-18 based on agent research.
Sections will be updated as drafting and team review proceed.
